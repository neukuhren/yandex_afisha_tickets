from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from src.parser.aggregator import TicketLot, TicketSnapshot, aggregate_hallplan

logger = logging.getLogger(__name__)


@dataclass
class ParsedWidgetUrl:
    event_id: int
    region_id: int
    client_key: str | None


@dataclass
class ParsedSessionWidgetUrl:
    session_key: str
    client_key: str
    region_id: int | None


@dataclass
class SessionInfo:
    key: str
    session_id: int
    name: str
    session_date: str
    venue_name: str
    venue_address: str
    available_seat_count: int
    sale_status: str
    presentation_date: str
    region_id: int = 47


@dataclass
class EventMeta:
    event_id: int
    name: str
    region_id: int
    client_key: str
    presentation_dates: list[str]
    sale_status: str


@dataclass
class AfishaPageInfo:
    title: str
    region_id: int
    source_url: str


@dataclass
class ResolvedEventInput:
    source_url: str
    widget_event_id: int
    region_id: int
    client_key: str | None
    title: str
    is_afisha_page: bool
    direct_session: SessionInfo | None = None


class AfishaParserError(Exception):
    pass


class AfishaClient:
    WIDGET_HOST = "https://widget.afisha.yandex.ru"
    AFISHA_HOST = "https://afisha.yandex.ru"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AfishaTicketBot/1.0)",
                "Accept-Encoding": "identity",
            },
            follow_redirects=True,
        )
        self._config_cache: dict[str, dict[str, Any]] = {}
        self._antibot_cache: dict[str, tuple[str, float, bool]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def parse_widget_url(url: str) -> ParsedWidgetUrl:
        parsed = urlparse(url)
        if "widget.afisha.yandex.ru" not in parsed.netloc:
            raise AfishaParserError("Это не ссылка на виджет Яндекс Афиши")

        match = re.search(r"/events/(\d+)", parsed.path)
        if not match:
            raise AfishaParserError("Не удалось извлечь ID события из ссылки виджета")

        query = parse_qs(parsed.query)
        region_raw = (query.get("regionId") or query.get("region_id") or ["47"])[0]
        client_key = (query.get("clientKey") or query.get("client_key") or [None])[0]

        return ParsedWidgetUrl(
            event_id=int(match.group(1)),
            region_id=int(region_raw),
            client_key=client_key,
        )

    @staticmethod
    def parse_widget_session_url(url: str) -> ParsedSessionWidgetUrl:
        parsed = urlparse(url)
        if "widget.afisha.yandex.ru" not in parsed.netloc:
            raise AfishaParserError("Это не ссылка на виджет Яндекс Афиши")

        match = re.search(r"/sessions/([^/?#]+)", parsed.path)
        if not match:
            raise AfishaParserError("Не удалось извлечь ключ сеанса из ссылки виджета")

        query = parse_qs(parsed.query)
        client_key = (query.get("clientKey") or query.get("client_key") or [None])[0]
        if not client_key:
            raise AfishaParserError("В ссылке виджета сеанса нужен параметр clientKey")

        region_raw = (query.get("regionId") or query.get("region_id") or [None])[0]
        region_id = int(region_raw) if region_raw else None

        return ParsedSessionWidgetUrl(
            session_key=match.group(1),
            client_key=client_key,
            region_id=region_id,
        )

    @staticmethod
    def decode_session_key(session_key: str) -> tuple[int, int, int, int]:
        try:
            decoded = base64.b64decode(session_key).decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise AfishaParserError("Некорректный ключ сеанса") from exc
        parts = decoded.split("|")
        if len(parts) < 4:
            raise AfishaParserError("Некорректный формат ключа сеанса")
        return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])

    async def resolve_afisha_url(self, url: str) -> ParsedWidgetUrl:
        response = await self._client.get(url)
        response.raise_for_status()
        html = response.text
        return self._parse_afisha_html(html, url)

    def _parse_afisha_html(self, html: str, url: str) -> ParsedWidgetUrl:
        widget_match = re.search(
            r"widget\.afisha\.yandex\.ru/w/events/(\d+)\?([^\"'\s]+)",
            html,
        )
        if widget_match:
            event_id = int(widget_match.group(1))
            query = parse_qs(widget_match.group(2))
            region_id = int((query.get("regionId") or query.get("region_id") or ["47"])[0])
            client_key = (query.get("clientKey") or query.get("client_key") or [None])[0]
            return ParsedWidgetUrl(event_id=event_id, region_id=region_id, client_key=client_key)

        client_key = self._extract_client_key(html)
        region_id = self._extract_region_id(html, url)
        event_id = self._extract_widget_event_id(html)
        if event_id > 0:
            return ParsedWidgetUrl(event_id=event_id, region_id=region_id, client_key=client_key)

        if re.search(r'"Event:\w+"\s*:\s*\{[^}]*"title"\s*:\s*"([^"]+)"', html):
            raise AfishaParserError(
                "Событие найдено на Афише, но билеты ещё не подключены к виджету."
            )

        raise AfishaParserError("Не удалось определить параметры события по ссылке Афиши")

    @staticmethod
    def _extract_client_key(html: str) -> str | None:
        match = re.search(r'"clientKey"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)"', html)
        return match.group(1) if match else None

    @staticmethod
    def _extract_widget_event_id(html: str) -> int:
        patterns = (
            r'"ticketsEventId"\s*:\s*(\d+)',
            r'widget\.afisha\.yandex\.ru/w/events/(\d+)',
            r'"tickets"\s*:\s*\[\s*\{\s*"id"\s*:\s*"(\d+)"',
            r'"ticket"\s*:\s*\{\s*"id"\s*:\s*"(\d+)"',
        )
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return int(match.group(1))
        return 0

    def _extract_region_id(self, html: str, url: str) -> int:
        city_match = re.search(r'"cityInfo\(\{\\"id\\":\\"([^"\\]+)\\"\}\)"', html)
        if city_match:
            return self._resolve_city_region_sync(city_match.group(1))
        path_match = re.search(r"afisha\.yandex\.ru/([a-z0-9-]+)/", url)
        if path_match:
            return self._resolve_city_region_sync(path_match.group(1))
        return 47

    async def resolve_afisha_page(self, url: str) -> AfishaPageInfo:
        response = await self._client.get(url)
        response.raise_for_status()
        html = response.text

        title_match = re.search(
            r'"Event:[^"]+"\s*:\s*\{[^}]*"title"\s*:\s*"([^"]+)"',
            html,
        )
        if not title_match:
            title_match = re.search(r"<title>Билеты на «([^»]+)»", html)
        title = title_match.group(1) if title_match else "Событие на Афише"

        region_id = self._extract_region_id(html, url)

        return AfishaPageInfo(title=title, region_id=region_id, source_url=url)

    async def resolve_event_input(self, url: str) -> ResolvedEventInput:
        if "widget.afisha.yandex.ru" in url and "/w/sessions/" in url:
            parsed_session = self.parse_widget_session_url(url)
            _, widget_event_id, _, _ = self.decode_session_key(parsed_session.session_key)
            await self._load_widget_config_from_url(url)
            session = await self.get_session_details(
                parsed_session.session_key,
                parsed_session.client_key,
                widget_event_id=widget_event_id,
            )
            region_id = parsed_session.region_id or session.region_id
            title = session.name or "Сеанс"
            return ResolvedEventInput(
                source_url=url,
                widget_event_id=widget_event_id,
                region_id=region_id,
                client_key=parsed_session.client_key,
                title=title,
                is_afisha_page=False,
                direct_session=session,
            )

        if "widget.afisha.yandex.ru" in url:
            parsed = self.parse_widget_url(url)
            meta = await self.get_event_meta(parsed.event_id, parsed.region_id, parsed.client_key)
            return ResolvedEventInput(
                source_url=url,
                widget_event_id=parsed.event_id,
                region_id=parsed.region_id,
                client_key=meta.client_key,
                title=meta.name,
                is_afisha_page=False,
            )

        try:
            parsed = await self.resolve_afisha_url(url)
            if parsed.event_id > 0:
                meta = await self.get_event_meta(parsed.event_id, parsed.region_id, parsed.client_key)
                return ResolvedEventInput(
                    source_url=url,
                    widget_event_id=parsed.event_id,
                    region_id=parsed.region_id,
                    client_key=meta.client_key,
                    title=meta.name,
                    is_afisha_page=True,
                )
        except AfishaParserError:
            pass

        page = await self.resolve_afisha_page(url)
        return ResolvedEventInput(
            source_url=url,
            widget_event_id=0,
            region_id=page.region_id,
            client_key=None,
            title=page.title,
            is_afisha_page=True,
        )

    async def discover_sessions(
        self, event_id: int, region_id: int, client_key: str | None
    ) -> tuple[EventMeta, list[SessionInfo]]:
        meta = await self.get_event_meta(event_id, region_id, client_key)
        if not meta.presentation_dates:
            return meta, []

        sessions = await self.list_sessions(
            meta.event_id,
            meta.region_id,
            meta.client_key,
            meta.presentation_dates[0],
            meta.presentation_dates[-1],
        )
        return meta, sessions

    async def try_resolve_widget_from_afisha(self, url: str) -> ParsedWidgetUrl | None:
        try:
            parsed = await self.resolve_afisha_url(url)
            if parsed.event_id > 0:
                return parsed
        except AfishaParserError:
            return None
        return None

    async def _resolve_city_region(self, city_slug: str) -> int:
        return self._resolve_city_region_sync(city_slug)

    @staticmethod
    def _resolve_city_region_sync(city_slug: str) -> int:
        city_map = {
            "moscow": 213,
            "saint-petersburg": 2,
            "spb": 2,
            "nizhny-novgorod": 47,
            "ekaterinburg": 54,
            "yekaterinburg": 54,
        }
        return city_map.get(city_slug, 47)

    async def _load_widget_config_from_url(self, widget_url: str) -> tuple[dict[str, str], str, str]:
        response = await self._client.get(widget_url)
        response.raise_for_status()
        html = response.text

        config_raw = self._extract_js_object(html, "window['__config'] = ")
        if not config_raw:
            raise AfishaParserError("Не удалось получить конфигурацию виджета")

        config = json.loads(config_raw.replace("undefined", "null"))
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AfishaTicketBot/1.0)",
            **config["fetch"]["defaultHeaders"],
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Referer": widget_url,
        }
        resolved_client_key = config["clientKey"]["id"]
        self._config_cache[resolved_client_key] = headers
        return headers, resolved_client_key, widget_url

    async def _load_widget_config(
        self, event_id: int, region_id: int, client_key: str | None
    ) -> tuple[dict[str, str], str, str]:
        if client_key:
            widget_url = (
                f"{self.WIDGET_HOST}/w/events/{event_id}"
                f"?regionId={region_id}&clientKey={client_key}"
            )
        else:
            widget_url = f"{self.WIDGET_HOST}/w/events/{event_id}?regionId={region_id}"

        return await self._load_widget_config_from_url(widget_url)

    def _headers_for(self, client_key: str) -> dict[str, str]:
        return self._config_cache.get(client_key, {"User-Agent": "Mozilla/5.0"})

    async def get_event_meta(
        self, event_id: int, region_id: int, client_key: str | None
    ) -> EventMeta:
        headers, resolved_key, _ = await self._load_widget_config(event_id, region_id, client_key)
        url = (
            f"{self.WIDGET_HOST}/api/tickets/v1/events/{event_id}"
            f"?clientKey={resolved_key}&region_id={region_id}&req_number=1"
        )
        response = await self._client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise AfishaParserError(f"Ошибка API события: {payload}")

        event = payload["result"]["event"]
        dates = [item["date"] for item in event.get("presentationSessions", [])]
        return EventMeta(
            event_id=event_id,
            name=event.get("name", "Событие"),
            region_id=region_id,
            client_key=resolved_key,
            presentation_dates=dates,
            sale_status=event.get("saleStatus", "unknown"),
        )

    async def list_sessions(
        self,
        event_id: int,
        region_id: int,
        client_key: str,
        date_from: str,
        date_to: str,
    ) -> list[SessionInfo]:
        headers = self._headers_for(client_key)
        url = (
            f"{self.WIDGET_HOST}/api/tickets/v1/events/{event_id}/venues/sessions"
            f"?clientKey={client_key}&offset=0&limit=50"
            f"&dateFrom={date_from}&dateTo={date_to}&regionId={region_id}&req_number=2"
        )
        response = await self._client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise AfishaParserError(f"Ошибка API сеансов: {payload}")

        sessions: list[SessionInfo] = []
        for venue in payload["result"]["venues"]["items"]:
            for session in venue.get("sessions", []):
                sessions.append(
                    SessionInfo(
                        key=session["key"],
                        session_id=session["id"],
                        name=session.get("name") or session.get("eventName", ""),
                        session_date=session.get("sessionDate", ""),
                        venue_name=venue.get("name", ""),
                        venue_address=venue.get("address", ""),
                        available_seat_count=int(session.get("availableSeatCount") or 0),
                        sale_status=session.get("saleStatus", "unknown"),
                        presentation_date=session.get("presentationSessionDate", date_from),
                    )
                )
        return sessions

    async def _ensure_widget_headers(
        self,
        client_key: str,
        widget_event_id: int | None,
        region_id: int | None,
        *,
        widget_url: str | None = None,
    ) -> dict[str, str]:
        if widget_url:
            await self._load_widget_config_from_url(widget_url)
        elif widget_event_id and region_id:
            await self._load_widget_config(widget_event_id, region_id, client_key)
        return self._headers_for(client_key)

    async def _read_json_response(self, response: httpx.Response) -> dict[str, Any]:
        if response.is_stream_consumed:
            raw = response.content
        else:
            raw = b"".join([chunk async for chunk in response.aiter_raw()])
        if not raw:
            raise AfishaParserError(f"Пустой ответ API (HTTP {response.status_code})")
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            preview = raw[:200].decode("utf-8", errors="replace")
            raise AfishaParserError(
                f"Ответ API не JSON (HTTP {response.status_code}): {preview}"
            ) from exc

    async def get_session_details(
        self,
        session_key: str,
        client_key: str,
        *,
        widget_event_id: int | None = None,
        region_id: int | None = None,
    ) -> SessionInfo:
        await self._ensure_widget_headers(
            client_key,
            widget_event_id,
            region_id,
            widget_url=f"{self.WIDGET_HOST}/w/sessions/{session_key}?clientKey={client_key}",
        )
        headers = self._headers_for(client_key)
        url = f"{self.WIDGET_HOST}/api/tickets/v1/sessions/{session_key}?clientKey={client_key}"
        response = await self._client.get(url, headers=headers)
        response.raise_for_status()
        payload = await self._read_json_response(response)
        if payload.get("status") != "success":
            raise AfishaParserError(f"Ошибка API сеанса: {payload}")

        session = payload["result"]["session"]
        venue = session.get("venue") or {}
        city = venue.get("city") or {}
        region_from_api = city.get("regionId") or city.get("region_id")
        event_block = session.get("event") or {}
        name = (
            session.get("name")
            or session.get("eventName")
            or event_block.get("name")
            or "Сеанс"
        )
        return SessionInfo(
            key=session_key,
            session_id=int(session["id"]),
            name=name,
            session_date=session.get("sessionDate", ""),
            venue_name=venue.get("name", ""),
            venue_address=venue.get("address", ""),
            available_seat_count=int(session.get("availableSeatCount") or 0),
            sale_status=session.get("saleStatus", "unknown"),
            presentation_date=session.get("presentationSessionDate", ""),
            region_id=int(region_from_api) if region_from_api else (region_id or 47),
        )

    async def _fetch_antibot_state(self, session_key: str, client_key: str) -> tuple[str, bool]:
        cached = self._antibot_cache.get(session_key)
        if cached and cached[1] > time.time():
            return cached[0], cached[2]

        headers = {
            **self._headers_for(client_key),
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
        }
        response = await self._client.post(
            f"{self.WIDGET_HOST}/api/antibot/check?clientKey={client_key}",
            headers=headers,
            json={"sessionKey": session_key},
        )
        response.raise_for_status()
        payload = await self._read_json_response(response)
        jwt = payload.get("jwt")
        if not jwt:
            raise AfishaParserError(f"Нет JWT в ответе antibot/check: {payload}")

        captcha_required = bool(
            payload.get("captchaRequired")
            or payload.get("authorizationStatus") == "captcha_required"
        )
        self._antibot_cache[session_key] = (jwt, time.time() + 3500, captcha_required)
        return jwt, captcha_required

    async def _load_session_api_payload(self, session_key: str, client_key: str) -> dict[str, Any]:
        headers = self._headers_for(client_key)
        url = f"{self.WIDGET_HOST}/api/tickets/v1/sessions/{session_key}?clientKey={client_key}"
        response = await self._client.get(url, headers=headers)
        response.raise_for_status()
        payload = await self._read_json_response(response)
        if payload.get("status") != "success":
            raise AfishaParserError(f"Ошибка API сеанса: {payload}")
        return payload["result"]["session"]

    @staticmethod
    def _summary_snapshot_from_session(session: dict[str, Any]) -> TicketSnapshot:
        available = int(session.get("availableSeatCount") or 0)
        sale_status = session.get("saleStatus", "unknown")
        if available <= 0:
            return TicketSnapshot(lots=[], sale_status=sale_status, total_count=0)

        prices = session.get("prices") or []
        if not prices:
            return TicketSnapshot(lots=[], sale_status=sale_status, total_count=available)

        lots: list[TicketLot] = []
        for item in prices:
            price_kopecks = int(item.get("value") or 0)
            if price_kopecks <= 0:
                continue
            lots.append(
                TicketLot(
                    sector="Сводка (карта зала недоступна)",
                    price_kopecks=price_kopecks,
                    fee_kopecks=0,
                    count=available,
                )
            )
        if not lots:
            return TicketSnapshot(lots=[], sale_status=sale_status, total_count=available)

        min_price = min(lot.price_kopecks for lot in lots)
        lots = [
            TicketLot(
                sector="Сводка (карта зала недоступна)",
                price_kopecks=min_price,
                fee_kopecks=0,
                count=available,
            )
        ]
        return TicketSnapshot(lots=lots, sale_status=sale_status, total_count=available)

    async def _fetch_session_summary_snapshot(
        self,
        session_key: str,
        client_key: str,
        session_sale_status: str,
    ) -> TicketSnapshot:
        session = await self._load_session_api_payload(session_key, client_key)
        snapshot = self._summary_snapshot_from_session(session)
        if snapshot.sale_status == "unknown":
            snapshot.sale_status = session_sale_status
        logger.warning(
            "Hallplan недоступен (капча/ограничение API), используем сводку сеанса: %s мест",
            snapshot.total_count,
        )
        return snapshot

    async def fetch_ticket_snapshot(
        self,
        session_key: str,
        client_key: str,
        session_sale_status: str,
        available_seat_count: int,
        *,
        widget_event_id: int | None = None,
        region_id: int | None = None,
        widget_url: str | None = None,
    ) -> TicketSnapshot:
        if available_seat_count <= 0 and session_sale_status in {"no-seats", "closed", "sold-out"}:
            return TicketSnapshot(lots=[], sale_status=session_sale_status, total_count=0)

        await self._ensure_widget_headers(
            client_key,
            widget_event_id,
            region_id,
            widget_url=widget_url,
        )
        antibot_jwt, captcha_required = await self._fetch_antibot_state(session_key, client_key)
        if captcha_required:
            return await self._fetch_session_summary_snapshot(
                session_key, client_key, session_sale_status
            )

        headers = {
            **self._headers_for(client_key),
            "Accept-Encoding": "identity",
            "X-Antibot-Token": antibot_jwt,
        }
        url = (
            f"{self.WIDGET_HOST}/api/tickets/v1/sessions/{session_key}/hallplan/async"
            f"?clientKey={client_key}"
        )
        async with self._client.stream("GET", url, headers=headers) as response:
            if response.status_code == 403:
                self._antibot_cache.pop(session_key, None)
                raise AfishaParserError("Доступ к hallplan запрещён (403)")
            payload = await self._read_json_response(response)
            if response.status_code >= 500:
                response.raise_for_status()

        if payload.get("status") != "success":
            code = payload.get("status_code") or payload.get("statusCode")
            if code in {"missing-antibot-token", "antibot-token-mismatch"}:
                self._antibot_cache.pop(session_key, None)
                antibot_jwt, captcha_required = await self._fetch_antibot_state(
                    session_key, client_key
                )
                if captcha_required:
                    return await self._fetch_session_summary_snapshot(
                        session_key, client_key, session_sale_status
                    )
                headers["X-Antibot-Token"] = antibot_jwt
                async with self._client.stream("GET", url, headers=headers) as response:
                    payload = await self._read_json_response(response)
                    if response.status_code >= 500:
                        response.raise_for_status()
            if payload.get("status") != "success":
                message = payload.get("message") or payload.get("status_code") or payload
                if message == "not-found":
                    return await self._fetch_session_summary_snapshot(
                        session_key, client_key, session_sale_status
                    )
                raise AfishaParserError(f"Ошибка hallplan: {message}")

        result = payload["result"]
        sale_status = result.get("saleStatus", session_sale_status)
        hallplan = result.get("hallplan")
        if not hallplan:
            return TicketSnapshot(lots=[], sale_status=sale_status, total_count=0)

        display_type = result.get("hallplanDisplayType", "with-seats")
        snapshot = aggregate_hallplan(hallplan, display_type)
        snapshot.sale_status = sale_status
        return snapshot

    @staticmethod
    def _extract_js_object(html: str, marker: str) -> str | None:
        start = html.find(marker)
        if start < 0:
            return None
        index = start + len(marker)
        if index >= len(html) or html[index] != "{":
            return None

        depth = 0
        in_string = False
        escaped = False
        for position in range(index, len(html)):
            char = html[position]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return html[index : position + 1]
        return None

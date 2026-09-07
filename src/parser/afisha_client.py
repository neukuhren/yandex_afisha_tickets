from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from src.parser.aggregator import TicketSnapshot, aggregate_hallplan


@dataclass
class ParsedWidgetUrl:
    event_id: int
    region_id: int
    client_key: str | None


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


class AfishaParserError(Exception):
    pass


class AfishaClient:
    WIDGET_HOST = "https://widget.afisha.yandex.ru"
    AFISHA_HOST = "https://afisha.yandex.ru"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; AfishaTicketBot/1.0)"},
            follow_redirects=True,
        )
        self._config_cache: dict[str, dict[str, Any]] = {}

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

    async def resolve_afisha_url(self, url: str) -> ParsedWidgetUrl:
        response = await self._client.get(url)
        response.raise_for_status()
        html = response.text

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

        event_match = re.search(r'"Event:\w+"\s*:\s*\{[^}]*"title"\s*:\s*"([^"]+)"', html)
        client_key_match = re.search(r'"clientKey"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)"', html)
        numeric_match = re.search(r'"ticketsEventId"\s*:\s*(\d+)', html)
        if not numeric_match:
            numeric_match = re.search(r'widget\.afisha\.yandex\.ru/w/events/(\d+)', html)

        city_match = re.search(r'"cityInfo\(\{\\"id\\":\\"([^"\\]+)\\"\}\)"', html)
        region_id = 47
        if city_match:
            region_id = await self._resolve_city_region(city_match.group(1))

        if numeric_match:
            return ParsedWidgetUrl(
                event_id=int(numeric_match.group(1)),
                region_id=region_id,
                client_key=client_key_match.group(1) if client_key_match else None,
            )

        if event_match:
            raise AfishaParserError(
                "Событие найдено на Афише, но билеты ещё не подключены к виджету."
            )

        raise AfishaParserError("Не удалось определить параметры события по ссылке Афиши")

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

        city_match = re.search(r'"cityInfo\(\{\\"id\\":\\"([^"\\]+)\\"\}\)"', html)
        region_id = 47
        if city_match:
            region_id = await self._resolve_city_region(city_match.group(1))

        return AfishaPageInfo(title=title, region_id=region_id, source_url=url)

    async def resolve_event_input(self, url: str) -> ResolvedEventInput:
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
        city_map = {
            "moscow": 213,
            "saint-petersburg": 2,
            "spb": 2,
            "nizhny-novgorod": 47,
            "ekaterinburg": 54,
        }
        return city_map.get(city_slug, 47)

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
            "Referer": widget_url,
        }
        resolved_client_key = config["clientKey"]["id"]
        self._config_cache[resolved_client_key] = headers
        return headers, resolved_client_key, widget_url

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

    async def fetch_ticket_snapshot(
        self,
        session_key: str,
        client_key: str,
        session_sale_status: str,
        available_seat_count: int,
    ) -> TicketSnapshot:
        if available_seat_count <= 0 and session_sale_status in {"no-seats", "closed", "sold-out"}:
            return TicketSnapshot(lots=[], sale_status=session_sale_status, total_count=0)

        headers = self._headers_for(client_key)
        url = (
            f"{self.WIDGET_HOST}/api/tickets/v1/sessions/{session_key}/hallplan/async"
            f"?clientKey={client_key}"
        )
        response = await self._client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise AfishaParserError(f"Ошибка hallplan: {payload}")

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

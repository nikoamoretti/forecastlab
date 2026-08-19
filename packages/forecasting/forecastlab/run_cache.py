from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from forecastlab.fetch import fetch_document
from forecastlab.providers.base import SearchProvider
from forecastlab.schemas import FetchedDocument, SearchHit
from forecastlab.timeutil import as_utc


def _as_of_key(value: datetime | None) -> str:
    if value is None:
        return ""
    return as_utc(value).isoformat()


@dataclass(frozen=True)
class RunCacheIdentity:
    run_id: str
    model_provider: str
    search_provider: str
    mode: str
    as_of: str
    configuration_hash: str


@dataclass
class RunCache:
    identity: RunCacheIdentity
    _search: dict[tuple[str, int], list[SearchHit]] = field(default_factory=dict)
    _fetch: dict[tuple[str, str, str, str], FetchedDocument] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        run_id: str | None,
        model_provider: str,
        search_provider: str,
        mode: str,
        as_of: datetime | None,
        configuration_hash: str,
    ) -> RunCache:
        return cls(
            identity=RunCacheIdentity(
                run_id=run_id or "ephemeral",
                model_provider=model_provider,
                search_provider=search_provider,
                mode=mode,
                as_of=_as_of_key(as_of),
                configuration_hash=configuration_hash or "none",
            )
        )

    def matches(
        self,
        *,
        run_id: str | None,
        model_provider: str,
        search_provider: str,
        mode: str,
        as_of: datetime | None,
        configuration_hash: str,
    ) -> bool:
        other = RunCache.create(
            run_id=run_id,
            model_provider=model_provider,
            search_provider=search_provider,
            mode=mode,
            as_of=as_of,
            configuration_hash=configuration_hash,
        ).identity
        return self.identity == other

    def search(self, provider: SearchProvider, query: str, max_results: int) -> list[SearchHit]:
        key = (query, max_results)
        if key not in self._search:
            self._search[key] = provider.search(query, max_results=max_results)
        return list(self._search[key])

    def fetch(self, url: str, **kwargs) -> FetchedDocument:
        as_of = kwargs.get("as_of")
        key = (
            url,
            kwargs.get("snapshot_url") or "",
            _as_of_key(as_of) if as_of is not None else "",
            str(kwargs.get("mode") or "live"),
        )
        if key not in self._fetch:
            self._fetch[key] = fetch_document(url, **kwargs)
        return self._fetch[key]

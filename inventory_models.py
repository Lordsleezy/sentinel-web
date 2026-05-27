from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


UNAVAILABLE_BLOCKED = "Unavailable — retailer blocked automated lookup"


@dataclass
class InventoryProgress:
    state: str
    provider: Optional[str] = None
    detail: str = ""
    at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InventoryProviderResult:
    provider: str
    status: str
    availability: str = ""
    price: str = ""
    product: str = ""
    location: str = ""
    source_url: str = ""
    confidence: float = 0.0
    error: Optional[str] = None
    elapsed_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InventorySearchResult:
    search_id: str
    user: str
    product: str
    location: str
    providers_checked: List[str]
    status: str
    cache_hit: bool
    results: List[InventoryProviderResult] = field(default_factory=list)
    progress: List[InventoryProgress] = field(default_factory=list)
    ai_parse: Dict[str, Any] = field(default_factory=dict)
    ai_summary: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    execution_time: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["results"] = [r.to_dict() if hasattr(r, "to_dict") else r for r in self.results]
        data["provider_results"] = data["results"]
        data["progress"] = [p.to_dict() if hasattr(p, "to_dict") else p for p in self.progress]
        return data

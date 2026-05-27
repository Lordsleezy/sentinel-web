from inventory_provider_base import BrowserInventoryProvider


class TargetBrowserProvider(BrowserInventoryProvider):
    name = "target"
    base_url = "https://www.target.com"
    search_domain = "target.com"
    search_path = "https://www.target.com/s?searchTerm={query}"

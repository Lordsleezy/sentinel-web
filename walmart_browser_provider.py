from inventory_provider_base import BrowserInventoryProvider


class WalmartBrowserProvider(BrowserInventoryProvider):
    name = "walmart"
    base_url = "https://www.walmart.com"
    search_domain = "walmart.com"
    search_path = "https://www.walmart.com/search?q={query}"

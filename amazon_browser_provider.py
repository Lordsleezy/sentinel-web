from inventory_provider_base import BrowserInventoryProvider


class AmazonBrowserProvider(BrowserInventoryProvider):
    name = "amazon"
    base_url = "https://www.amazon.com"
    search_domain = "amazon.com"
    search_path = "https://www.amazon.com/s?k={query}"

from inventory_provider_base import BrowserInventoryProvider


class BestBuyBrowserProvider(BrowserInventoryProvider):
    name = "bestbuy"
    base_url = "https://www.bestbuy.com"
    search_domain = "bestbuy.com"
    search_path = "https://www.bestbuy.com/site/searchpage.jsp?st={query}"

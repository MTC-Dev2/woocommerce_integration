import frappe
from frappe.utils.data import cint
from woocommerce import API as WCAPI


class WooCommerceConnector:
    def __init__(self, setup: dict):
        self.settings = setup
        self.url = self.settings.url
        self.consumer_key = self.settings.consumer_key
        self.consumer_secret = self.settings.get_password("consumer_secret")
        self.woocommerce = WCAPI(
            url=self.url,
            consumer_key=self.consumer_key,
            consumer_secret=self.consumer_secret,
            wp_api=True,
            verify_ssl=self.settings.verify_ssl,
            version="wc/v3",
            timeout=1000,
        )

    def _request(self, method, endpoint, data=None, params=None, **kwargs):
        woocomm_method = method.lower()
        positional_args = (
            (
                endpoint,
                data,
            )
            if woocomm_method in ("post", "put")
            else (endpoint,)
        )

        kwargs["params"] = params
        response = self.woocommerce.__getattribute__(woocomm_method)(
            *positional_args, **kwargs
        )
        response.raise_for_status()
        return response

    def get_products(self, **kwargs):
        response = self._request("GET", "products", params=kwargs)
        return response.json()

    def get_product(self, id: str):
        response = self._request("GET", f"products/{id}")
        return response.json()

    def create_product(self, product_data: dict):
        response = self._request("POST", "products", data=product_data)
        return response.json()

    def update_product(self, id: str, product_data: dict):
        response = self._request("PUT", f"products/{id}", data=product_data)
        return response.json()

    def batch_update_products(self, product_data: dict):
        try:
            response = self._request("POST", "products/batch", data=product_data)
            frappe.log_error(title="Response batch_update_products:WooCommerceConnector", 
                             message=f"Response:\n{str(response.json())}")

            if response.json():
                frappe.log_error(title="Finished batch_update_products:WooCommerceConnector", message=str(response.json()))
            return response.json()
        except Exception as ex:
            frappe.log_error(title="Error batch_update_products:WooCommerceConnector", message=frappe.get_traceback())

    def batch_update_variations_products(self, variation_products_data: dict):
        try:
            full_response = ""
            for product_id in variation_products_data:
                response = self._request("POST", f"products/{product_id}/variations/batch", data=variation_products_data[product_id])
                frappe.log_error(title="Response batch_update_variations_products:WooCommerceConnector", 
                             message=f"Response:\n{str(response.json())}")

                if response.json():
                    full_response += str(response.json()) + "\n"
            
            frappe.log_error(title="Finished batch_update_variations_products:WooCommerceConnector", message=full_response)
            return full_response
        except Exception as ex:
            frappe.log_error(title="Error batch_update_variations_products:WooCommerceConnector", message=frappe.get_traceback())



    def delete_product(self, id: str):
        response = self._request("DELETE", f"products/{id}")
        return response.json()

    def get_orders(self, **kwargs):
        response = self._request("GET", "orders", params=kwargs)
        yield from response.json()

        pages = cint(response.headers.get("X-WP-TotalPages") or 1)
        for page_idx in range(1, pages):
            response = self._request(
                "GET", "orders", params={**kwargs, "page": page_idx + 1}
            )
            yield from response.json()

    def get_order(self, id: str):
        return self._request("GET", f"orders/{id}").json()

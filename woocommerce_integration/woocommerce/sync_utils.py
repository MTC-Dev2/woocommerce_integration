import frappe
from frappe.utils import cint, get_datetime

from woocommerce_integration.general_utils import (
    get_woocommerce_setup,
    update_woocommerce_sync,
)
from woocommerce_integration.order_creation_utils import create_sales_order
from woocommerce_integration.woocommerce_connector import WooCommerceConnector


@frappe.whitelist()
def batch_sync_stock():
    """
    Flow: From ERPNext to WooCommerce.
    Called by the scheduler. Batch update items from all recent stock updates.
    """
    setup = get_woocommerce_setup()
    setup.check_permission("write")
    if not setup.enable_stock_sync:
        return

    filters = {"warehouse": setup.warehouse}
    if setup.last_stock_sync:
        filters["modified"] = (">=", setup.last_stock_sync)

    # Get all recent Bins
    data = {"update": []}
    for row in frappe.get_all(
        "Bin", filters=filters, fields=["item_code", "actual_qty"]
    ):
        if product_id := frappe.db.get_value(
            "Item", row.item_code, "woocomm_product_id"
        ):
            data["update"].append(
                {
                    "id": product_id,
                    "stock_quantity": cint(row.actual_qty),
                    "manage_stock": True,
                }
            )

    # Update stock in WooCommerce
    if data["update"]:
        connector = WooCommerceConnector(setup)
        connector.batch_update_products(data)
        update_woocommerce_sync("last_stock_sync", get_datetime())


@frappe.whitelist()
def batch_sync_order():
    """Batch sync orders from WooCommerce to ERPNext."""
    setup = get_woocommerce_setup()
    setup.check_permission("write")

    if not setup.enable_order_sync:
        return
    last_sync_datetime = None
    for order in get_woocommerce_orders():
        last_sync_datetime = order.get("date_modified")
        # print(f">>>> order: {order} <<<")
        
        create_sales_order(order, setup)
    if last_sync_datetime:
        update_woocommerce_sync("last_order_sync", last_sync_datetime)


def get_woocommerce_orders():
    """Get all the new orders from WooCommerce."""
    setup = get_woocommerce_setup()
    woocommerce = WooCommerceConnector(setup)

    last_sync_datetime = (
        get_datetime(setup.last_order_sync).isoformat()
        if setup.last_order_sync
        else None
    )
    
    per_page = cint(setup.order_per_page) or 10
    return woocommerce.get_orders(
        per_page=per_page,
        modified_after=last_sync_datetime,
        status=setup.order_status_filters,
        orderby="modified",
        order="asc",
    )

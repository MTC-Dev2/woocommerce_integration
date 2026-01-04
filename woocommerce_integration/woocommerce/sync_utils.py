import frappe
from frappe.utils import cint, get_datetime, now, get_datetime_str, getdate

from woocommerce_integration.general_utils import (
    get_woocommerce_setup,
    update_woocommerce_sync,
)
from woocommerce_integration.order_creation_utils import create_sales_order
from woocommerce_integration.woocommerce_connector import WooCommerceConnector


def enqueue_batch_sync_stock():
    try:
        frappe.enqueue(method=batch_sync_stock, queue="short", timeout=1500)
    except Exception as ex:
        frappe.log_error(title=f"Error enqueue_batch_sync_stock:sync_utils", message=frappe.get_traceback())

@frappe.whitelist()
def batch_sync_stock():
    """
    Flow: From ERPNext to WooCommerce.
    Called by the scheduler. Batch update items from all recent stock updates.
    get 100 itms from Stock Ledger Entry every call.
    """
    try:
        data_to_return = {}
        setup = get_woocommerce_setup()
        setup.check_permission("write")
        if not setup.enable_stock_sync:
            return        
        frappe.log_error(title=f">>>>> Start batch_sync_stock: {now()} <<<<<", message=f">>>>> Start batch_sync_stock: {now()} <<<<<")

        # filters = {
        #     "warehouse": setup.warehouse,
        #     "custom_woocomm_synced": 0
        # }
        if setup.last_stock_sync:
            date_filter = get_datetime_str(getdate(setup.last_stock_sync))
            # filters["modified"] = (">=", get_datetime_str(getdate(setup.last_stock_sync)))
        else:
            date_filter = get_datetime_str(getdate())
            # filters["modified"] = (">=", get_datetime_str(getdate()))

        variation_products_data = {}
        variation_data = {"update": []}
        data = {"update": []}

        # Get all recent stock ledger entry
        # sle_docs = frappe.get_all("Stock Ledger Entry", filters=filters, 
        #                           fields=["name", "item_code", "qty_after_transaction"], 
        #                           order_by="creation DESC", group_by="item_code", limit=100)
        
        sle_docs = frappe.db.sql(f"""SELECT 
                                            SLE.name as name, 
                                            SLE.item_code as item_code, 
                                            SLE.qty_after_transaction as qty_after_transaction
    
                                    FROM `tabStock Ledger Entry` SLE
                                    INNER JOIN `tabItem` ITM ON ITM.name = SLE.item_code

                                    INNER JOIN ( SELECT item_code, MAX(creation) AS max_creation
                                        FROM `tabStock Ledger Entry`
                                        WHERE warehouse = '{setup.warehouse}'
                                        AND custom_woocomm_synced = 0
                                        AND posting_date >= '{date_filter}'
                                        GROUP BY item_code
                                    ) latest ON latest.item_code = SLE.item_code AND latest.max_creation = SLE.creation

                                    WHERE
                                        ITM.custom_woocommerce_product_type IS NOT NULL
                                        OR ITM.woocomm_product_id IS NOT NULL
                                        OR ITM.custom_woocommerce_parent_product_id IS NOT NULL
                                    ORDER BY SLE.posting_date DESC
                                    LIMIT 100; """, as_dict=1, debug=1) 

        if not sle_docs:
            frappe.log_error(title=f">>>>> End batch_sync_stock (No SLE docs): {now()} <<<<<", 
                             message=f">>>>> End batch_sync_stock (No Stock Ledger Entry docs found): {now()} <<<<<")
            return
        
        data_to_return["sle_docs"] = sle_docs

        for row in sle_docs:
            sle_doc = frappe.get_doc("Stock Ledger Entry", row.name)

            if frappe.db.exists("Item", {"name": row.item_code}):
                item_doc = frappe.get_doc("Item", row.item_code)        
                product_type = item_doc.get("custom_woocommerce_product_type") or None

                if product_type and product_type == "variation":
                    product_parent_id = item_doc.get("custom_woocommerce_parent_product_id") or None
                    product_variation_id = item_doc.get("woocomm_product_id")
                
                    if product_parent_id and product_variation_id:
                        variation_data["update"].append(
                                {
                                    "id": product_variation_id,
                                    "stock_quantity": cint(row.qty_after_transaction),
                                    "manage_stock": 1,
                                }
                            )
                        variation_products_data[str(product_parent_id)] = variation_data
                        
                        sle_doc.db_set("custom_woocomm_synced", 1)
                        frappe.db.commit()
                else:
                    product_id = item_doc.get("woocomm_product_id")
                    if product_id:
                        data["update"].append(
                                {
                                    "id": product_id,
                                    "stock_quantity": cint(row.qty_after_transaction),
                                    "manage_stock": 1,
                                }
                            )
                        
                        sle_doc.db_set("custom_woocomm_synced", 1)
                        frappe.db.commit()
        
        # Update stock in WooCommerce
        if data["update"]:
        
            data_to_return["data_update"] = data
        
            connector = WooCommerceConnector(setup)
            connector.batch_update_products(data)
            # update_woocommerce_sync("last_stock_sync", get_datetime())
        if variation_products_data:

            data_to_return["variation_products_data"] = variation_products_data

            connector = WooCommerceConnector(setup)
            connector.batch_update_variations_products(variation_products_data)
            # update_woocommerce_sync("last_stock_sync", get_datetime())

        frappe.log_error(title=f">>>>> End batch_sync_stock: {now()} <<<<<", message=f">>>>> End batch_sync_stock: {now()} <<<<<")

        return data_to_return
    except Exception as ex:
        frappe.log_error(title="Error batch_sync_stock:sync_utils", message=frappe.get_traceback())
        # raise ex

@frappe.whitelist()
def reset_woocomm_synced_flag():
    try:
        frappe.db.sql("UPDATE `tabStock Ledger Entry` SET custom_woocomm_synced = 0")
        frappe.db.commit()
        return frappe.db.count("Stock Ledger Entry", {"custom_woocomm_synced": 1})
    except Exception as ex:
        frappe.log_error(title="Error reset_woocommerce_sync:sync_utils", message=frappe.get_traceback())
        # raise ex

@frappe.whitelist()
def batch_sync_order():
    """Batch sync orders from WooCommerce to ERPNext."""
    try:
        setup = get_woocommerce_setup()
        setup.check_permission("write")

        if not setup.enable_order_sync:
            return

        frappe.log_error(title=f">>>>> Start batch_sync_order: {now()} <<<<<", 
                         message=f">>>>> Start batch_sync_order: {now()} <<<<<")

        last_sync_datetime = None
        for order in get_woocommerce_orders():
            last_sync_datetime = get_datetime(order.get("date_modified")) if order.get("date_modified") else get_datetime()

            create_sales_order(order, setup)
        if last_sync_datetime:
            update_woocommerce_sync("last_order_sync", last_sync_datetime)

        frappe.log_error(title=f">>>>> End batch_sync_order: {now()} <<<<<", 
                         message=f">>>>> End batch_sync_order: {now()} <<<<<")
    except Exception as ex:
        frappe.log_error(title="Error batch_sync_order:sync_utils", message=frappe.get_traceback())
        # raise ex

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

def enqueue_get_woocommerce_product_ids():
    try:
        frappe.enqueue(method=get_woocommerce_product_ids, queue="long", timeout=1500)
    except Exception as ex:
        frappe.log_error(title=f"Error enqueue_get_woocommerce_product_ids:sync_utils", message=frappe.get_traceback())

def get_woocommerce_product_ids():
    try:
        setup = get_woocommerce_setup()
        woocommerce = WooCommerceConnector(setup)
        next_page = True
        page_num = 1

        frappe.log_error(title=f">>>>> Start get_woocommerce_product_ids: {now()} <<<<<", 
                         message=f">>>>> Start get_woocommerce_product_ids: {now()} <<<<<")

        while next_page:
            woocomm_items = woocommerce.get_products(page=page_num)
            if not woocomm_items:
                next_page = False
                continue

            for woocomm_item in woocomm_items:
                product_type = woocomm_item.get("type")
                if product_type == "variation":
                    if woocomm_item.get('sku') and woocomm_item.get('id') and woocomm_item.get('parent_id'):
                        if frappe.db.exists("Item", {"name": woocomm_item.get('sku')}):
                            item_doc = frappe.get_doc("Item", woocomm_item.get('sku'))

                            item_doc.woocomm_product_id = woocomm_item.get("id")
                            item_doc.custom_woocommerce_parent_product_id = woocomm_item.get("parent_id")
                            item_doc.custom_woocommerce_product_type = product_type
                            
                            item_doc.flags.ignore_mandatory = True
                            item_doc.save()
                elif product_type == "simple":
                    if woocomm_item.get('sku') and woocomm_item.get('id'):
                        if frappe.db.exists("Item", {"name": woocomm_item.get('sku')}):
                            item_doc = frappe.get_doc("Item", woocomm_item.get('sku'))

                            item_doc.woocomm_product_id = woocomm_item.get("id")
                            item_doc.custom_woocommerce_product_type = product_type
                            
                            item_doc.flags.ignore_mandatory = True
                            item_doc.save()
                elif product_type == "variable":
                    if woocomm_item.get('id'):
                        if frappe.db.exists("Item", {"name": woocomm_item.get('id')}):
                            item_doc = frappe.get_doc("Item", woocomm_item.get('id'))

                            item_doc.woocomm_product_id = woocomm_item.get("id")
                            item_doc.custom_woocommerce_product_type = product_type
                            
                            item_doc.flags.ignore_mandatory = True
                            item_doc.save()

            page_num += 1
            frappe.db.commit()

        frappe.log_error(title=f">>>>> End get_woocommerce_product_ids: {now()} <<<<<", 
                         message=f">>>>> End get_woocommerce_product_ids: {now()} <<<<<")
    except Exception as ex:
        frappe.log_error(title=f"Error get_woocommerce_product_ids:sync_utils", message=frappe.get_traceback())

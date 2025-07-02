from datetime import datetime

import frappe
from frappe import _
from frappe.utils import getdate, get_date_str, cstr, flt, add_days, validate_email_address, validate_phone_number

from erpnext.stock.get_item_details import get_item_price
from frappe.query_builder.functions import IfNull 
from woocommerce_integration.general_utils import get_woocommerce_setup


def create_sales_order(order_data: dict, setup: dict):
    """Create a sales order with its dependencies."""
    if not setup:
        setup = get_woocommerce_setup()
    try:
        frappe.log_error(title=f">>>>> Working with Order ID: {order_data.get('id')} <<<<<<", message=f"""{frappe.as_json(order_data)}""")
        customer = create_update_customer(order_data)
        if customer:
            create_order(order_data, setup, customer.name)
    except Exception:
        frappe.log_error(title="Error create_sales_order:order_creation_utils", message=frappe.get_traceback())
        # raise
    finally:
        frappe.log_error(f">>>>> End of Working with Order ID: {order_data.get('id')} <<<<<<")


def create_update_customer(order_data: dict):
    """Create or update a customer based on the order data."""
    try:
        billing_data = order_data.get("billing") or None
        if not billing_data:
            frappe.log_error(title=f"""Error no billing data found for customer with order: {order_data.get("id")}""", 
                            message=f"""Working with woocommerce_default_customer for order_id: {order_data.get("id")}""")
            customer = get_woocommerce_default_customer()
            return customer

        filtrs = {"customer_group": validate_customer_group()}

        customer_name = billing_data.get("first_name") + " " + billing_data.get("last_name")
        if customer_name:
            filtrs["customer_name"] = customer_name     
        
        customer_id = int(order_data.get("customer_id"))
        if customer_id:
            filtrs["woocomm_customer_id"] = customer_id     

        customer_state = billing_data.get("state")
        if customer_state:
            filtrs["territory"] = customer_state   

        customer_email = billing_data.get("email")
        if customer_email:
            filtrs["email"] = customer_email   

        # Customer could have been created manually which may differ in naming
        if (filtrs) and (erp_customer := frappe.db.exists("Customer", filtrs)):
            customer = frappe.get_doc("Customer", erp_customer)
        else:
            if customer_name:
                customer = frappe.new_doc("Customer")
                customer.name = customer_name #customer_id

                customer.customer_name = customer_name
                customer.customer_group = validate_customer_group()
                customer.email = billing_data.get("email") or None
                customer.territory = billing_data.get("state") or None
                if customer_id:
                    customer.woocomm_customer_id = customer_id

                customer.flags.ignore_mandatory = True
                customer.save()
                frappe.db.commit()

                # Create address/contact if does not exist
                create_address(billing_data, customer, "Billing", order_data.get("id"))
                create_address(order_data.get("shipping"), customer, "Shipping", order_data.get("id"))
                create_contact(billing_data, customer, order_data.get("id"))
                frappe.db.commit()
            else:
                customer = get_woocommerce_default_customer()
                return customer
            
        return customer
    except Exception as ex:
        frappe.log_error(title="Error create_update_customer:order_creation_utils", 
                         message=f"""order_id: {order_data.get("id")}\n\n{frappe.get_traceback()}""")

def get_woocommerce_default_customer():
    if erp_customer := frappe.db.exists("Customer", {"customer_name": "woocommerce_default_customer"}):
        return frappe.db.get_values("Customer", erp_customer, ["*"], as_dict=True)[0]
    return create_default_customer()

def create_default_customer():
    customer = frappe.new_doc("Customer")
    customer.name = "woocommerce_default_customer"
    customer.customer_name = "woocommerce_default_customer"
    customer.flags.ignore_mandatory = True
    customer.save()
    frappe.db.commit()
    return customer

def validate_customer_group():
    if woocomm_customer_group := frappe.db.exists("Customer Group", "iCenter E-Commerce"):
        return frappe.get_doc("Customer Group", woocomm_customer_group).name
    return create_default_customer_group()

def create_default_customer_group():
    customer_group = frappe.new_doc("Customer Group")
    customer_group.customer_group_name = "iCenter E-Commerce"
    
    customer_group.flags.ignore_mandatory = True
    customer_group.save()
    return customer_group.name


def create_address(raw_data: dict, customer: dict, address_type: str, order_id = None):
    try:
        """Create an address for the customer if it does not exist."""
        filtrs = {
            "pincode": raw_data.get("postcode"),
            "address_line1": raw_data.get("address_1", "Not Provided"),
            "address_type": address_type,
            "address_title": customer.get("customer_name") + "-" + address_type,
            # "address_line2": raw_data.get("address_2", "Not Provided"),  
        }
        if customer.woocomm_customer_id:
            filtrs["woocomm_customer_id"] = customer.woocomm_customer_id

        if frappe.db.exists("Address", filtrs):
            address_doc = frappe.get_doc("Address", filtrs)
            validate_address_links(address_doc, "Customer", customer.name)
            return

        address = frappe.new_doc("Address")
        address.address_title = customer.get("customer_name")
        address.address_line1 = raw_data.get("address_1", "Not Provided")
        address.address_line2 = raw_data.get("address_2", "Not Provided")
        address.city = raw_data.get("city", "Not Provided")
        if customer.woocomm_customer_id:
            address.woocomm_customer_id = customer.woocomm_customer_id
        address.address_type = address_type
        address.state = raw_data.get("state")
        address.pincode = raw_data.get("postcode")
        address.custom_additional_data = ""

        if validate_phone_number(raw_data.get("phone")):
            address.phone = raw_data.get("phone")
        else:
            address.custom_additional_data += "phone: " + str(raw_data.get("phone")) + "\n"

        if validate_email_address(raw_data.get("email")):
            address.email_id = raw_data.get("email")
        else:
            address.custom_additional_data += "email: " + str(raw_data.get("email")) + "\n"

        if country := raw_data.get("country"):
            address.country = frappe.db.get_value("Country", {"code": country.lower()})
        else:
            address.country = frappe.get_system_settings("country")

        # customer_link = {"link_doctype": "Customer", "link_name": customer.name}
        # address.append("links", customer_link)
        validate_address_links(address, "Customer", customer.name)

        address.flags.ignore_mandatory = True
        address.save()
        frappe.db.commit()
    except Exception as ex:
        frappe.log_error(title="Error create_address:order_creation_utils", 
                         message=f"""order_id: {order_id}\n\n{frappe.get_traceback()}""")

def validate_address_links(address_doc, link_doctype, link_name):
    for address_link in address_doc.get("links"):
        if address_link.link_doctype == link_doctype and address_link.link_name == link_name:
            return
    address_doc.append("links", {"link_doctype": link_doctype, "link_name": link_name})

def create_contact(data: dict, customer: str, order_id = None):
    try:
        email = data.get("email")
        phone = data.get("phone")
        if (not email) and (not phone):
            return

        filtrs = {
            "email_id": email,
            "first_name": data.get("first_name"),
            # "last_name": data.get("last_name"),
        }
        if customer.woocomm_customer_id:
            filtrs["woocomm_customer_id"] = customer.woocomm_customer_id

        if frappe.db.exists("Contact", filtrs):
            contact_doc = frappe.get_doc("Contact", filtrs)
            validate_contact_links(contact_doc, "Customer", customer.name)
            return

        contact = frappe.new_doc("Contact")
        contact.first_name = data.get("first_name")
        contact.last_name = data.get("last_name")
        contact.email_id = email
        if customer.woocomm_customer_id:
            contact.woocomm_customer_id = customer.woocomm_customer_id
        contact.is_primary_contact = 1
        contact.is_billing_contact = 1
        contact.custom_additional_data = ""

        if validate_phone_number(phone):
            contact.add_phone(phone, is_primary_mobile_no=1, is_primary_phone=1)
        else:
            contact.custom_additional_data += "phone: " + str(phone) + "\n"

        if validate_email_address(email):
            contact.add_email(email, is_primary=1)
        else:
            contact.custom_additional_data += "email: " + str(email) + "\n"

        
        # if phone:
        #     if customer.get("customer_details"):
        #         # customer.customer_details += f"""phone: {phone} \n"""
        #         customer.db_set("customer_details", customer.customer_details + f"""\nphone: {phone} \n""")
        #     else:
        #         # customer.customer_details = f"""phone: {phone} \n"""
        #         customer.db_set("customer_details", f"""phone: {phone} \n""")

        #     contact.add_phone(phone, is_primary_mobile_no=1, is_primary_phone=1)

        # if email:
        #     if customer.get("customer_details"):
        #         # customer.customer_details +=  f"""email: {email} \n"""
        #         customer.db_set("customer_details", customer.customer_details + f"""\nemail: {email} \n""")

        #     else:
        #         # customer.customer_details =  f"""email: {email} \n"""
        #         customer.db_set("customer_details", f"""email: {email} \n""")
            
        #     contact.add_email(email, is_primary=1)

        # contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
        validate_contact_links(contact, "Customer", customer.name)
        contact.flags.ignore_mandatory = True
        contact.save()
        frappe.db.commit()
    except Exception as ex:
        frappe.log_error(title="Error create_contact:order_creation_utils", 
                         message=f"""order_id: {order_id}\n\n{frappe.get_traceback()}""")

def validate_contact_links(contact_doc, link_doctype, link_name):
    for contact_link in contact_doc.get("links"):
        if contact_link.link_doctype == link_doctype and contact_link.link_name == link_name:
            return
    contact_doc.append("links", {"link_doctype": link_doctype, "link_name": link_name})

def create_order(order: dict, woocommerce_setup: dict, customer: str):
    """Create a sales order based on the order data."""
    try:
        # check if order already created using WooCommerce Order ID
        if sales_order := frappe.db.exists("Sales Order", {"woocomm_order_id": order.get("id")}):
            pass
        else:
            sales_order = frappe.new_doc("Sales Order")
            sales_order.customer = customer
            sales_order.company = woocommerce_setup.default_company

            sales_order.po_no = sales_order.woocomm_order_id = order.get("id")
            sales_order.custom_woocommerce_order_status = order.get("status", "")

            sales_order.naming_series = woocommerce_setup.sales_order_series

            created_date = datetime.fromisoformat(order.get("date_created")).date()
            sales_order.transaction_date = created_date
            sales_order.delivery_date = add_days(created_date, woocommerce_setup.delivery_after or 7)

            add_items_to_sales_order(order, sales_order, woocommerce_setup)

            sales_order.flags.ignore_mandatory = True

            if sales_order.get("items"):
                sales_order.save()

                # sales_order.insert(ignore_permissions=True)
                # sales_order.submit()
            else:
                frappe.log_error(title=f"""Error no items found for order: {order.get("id")}""", 
                                 message=f"""no items found for order: {order.get("id")}""")

            frappe.db.commit()
    except Exception as ex:
        frappe.log_error(title="Error create_order:order_creation_utils", message=frappe.get_traceback())
        # raise

def add_items_to_sales_order(order: dict, sales_order: dict, setup: dict):
    """Set the items in the sales order with taxes based on the order data."""
    try:
        if not order.get("line_items"):
            item = get_default_item()
            sales_order.append(
                    "items",
                    {
                        "item_code": item.name,
                        "item_name": item.item_name,
                        "description": item.description,
                        "delivery_date": sales_order.delivery_date,
                        "uom": "Nos",
                        "qty": 1,
                        "rate": 0,
                        "warehouse": setup.default_warehouse,
                    },
                )
        else:
            line_items = order.get("line_items") or []
            for line_item in line_items:
                item = get_item(line_item, setup)
                item_selling_rate = get_item_selling_rate(item, sales_order.transaction_date, order.get("currency"))

                sales_order.append(
                    "items",
                    {
                        "item_code": item.name,
                        "item_name": item.item_name,
                        "description": item.description,
                        "delivery_date": sales_order.delivery_date,
                        "uom": get_uom(line_item.get("sku"), setup.default_uom),
                        "qty": line_item.get("quantity"),
                        "rate": float(item_selling_rate[0][0]) if item_selling_rate else line_item.get("price"),
                        "warehouse": setup.default_warehouse,
                    },
                )
                if ordered_items_tax := flt(line_item.get("total_tax")):
                    add_tax_details(sales_order, ordered_items_tax, "Item Tax", setup.tax_account)

            add_tax_details(sales_order, flt(order.get("shipping_tax")), "Shipping Tax", setup.shipping_tax_account)
            add_tax_details(sales_order, flt(order.get("shipping_total")), "Shipping Total", setup.shipping_tax_account)
    except Exception:
        frappe.log_error(title="Error add_items_to_sales_order:order_creation_utils", message=frappe.get_traceback())
        # raise

def get_item_selling_rate(item, transaction_date, currency="IQD"):
    item_selling_rate = price_list_names = None 
    
    if frappe.db.exists("Price List", {"enabled": 1, "currency": currency, "selling": 1}):
        price_list_names = frappe.db.get_values("Price List", {"enabled": 1, "currency": currency, "selling": 1}, ["name"], pluck="name")

    if price_list_names:
        ip = frappe.qb.DocType("Item Price")
        query = (
            frappe.qb.from_(ip).select(ip.price_list_rate)
            .where((ip.item_code == item.name) & (ip.price_list.isin(price_list_names)))
            .orderby(IfNull(ip.valid_from, ip.creation), order=frappe.qb.desc)
            .orderby(ip.uom, order=frappe.qb.desc)
            .limit(1)
        )
        query = query.where(
            (IfNull(ip.valid_from, ip.creation) <= get_date_str(transaction_date))
            & (IfNull(ip.valid_upto, "2500-12-31") >= get_date_str(transaction_date))
        )
        item_selling_rate = query.run()
        return item_selling_rate

def get_default_item():
    if erp_item := frappe.db.exists("Item", {"item_code": "woocommerce_default_item"}):
        return frappe.db.get_values("Item", erp_item, ["name", "item_name", "description"], as_dict=True)[0]

    return create_default_item()

def create_default_item():
    item = frappe.new_doc("Item")
    item.item_code = "woocommerce_default_item"
    item.item_name = "woocommerce_default_item"
    item.stock_uom = "Nos"
    item.item_group = "WooCommerce Products"
    item.flags.ignore_mandatory = True
    item.save()

    return item

def get_item(item_data: dict, setup: dict) -> dict:
    """Get item document or create it if it does not exist."""
    woo_com_id = item_data["product_id"]
    woo_com_item_sku = item_data.get("sku") or None
    
    if woo_com_item_sku:
        if erp_item := frappe.db.exists("Item", {"item_code": woo_com_item_sku}):
            return frappe.db.get_values("Item", erp_item, ["name", "item_name", "description"], as_dict=True)[0]
    else:
        if erp_item := frappe.db.exists("Item", {"item_code": woo_com_id}):
            return frappe.db.get_values("Item", erp_item, ["name", "item_name", "description"], as_dict=True)[0]
    return create_item(item_data, woo_com_id, setup)

def create_item(item_data: dict, woo_com_id: str, setup: dict):
    """Create an item based on the item data."""
    item = frappe.new_doc("Item")
    item.item_code = item_data.get("sku") if item_data.get("sku") else cstr(woo_com_id)
    item.item_name = item_data.get("name")
    item.stock_uom = get_uom(item_data.get("sku"), setup.default_uom)
    item.item_group = "WooCommerce Products"
    item.image = (item_data.get("image") or {}).get("src")
    item.woocomm_product_id = cstr(woo_com_id)
    item.flags.ignore_mandatory = True
    item.save()

    return item

def get_uom(sku: str | None, default_uom: str):
    """Get the SKU from WooCommerce or the default UOM for the item."""
    if sku and not frappe.db.exists("UOM", sku):
        frappe.get_doc({"doctype": "UOM", "uom_name": sku}).save()

    return sku or (default_uom or "Nos")

def add_tax_details(sales_order, price, desc, tax_account_head):
    if not price:
        return
    sales_order.append(
        "taxes",
        {
            "charge_type": "Actual",
            "account_head": tax_account_head,
            "tax_amount": price,
            "description": desc,
        },
    )


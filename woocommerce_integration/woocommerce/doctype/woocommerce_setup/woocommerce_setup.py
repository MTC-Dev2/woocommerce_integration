# Copyright (c) 2024, ALYF GmbH and contributors
# For license information, please see license.txt
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

from woocommerce_integration.webhooks import ACTION_MAP
from woocommerce_integration.general_utils import get_woocommerce_setup


class WooCommerceSetup(Document):
    @property
    def order_status_filters(self):
        return [row.status for row in self.order_status]

    def onload(self):
        series = (
            frappe.get_meta("Sales Order").get_options("naming_series") or "SO-WOO-"
        )
        self.set_onload("sales_order_series", series)

    def before_validate(self):
        self.set_webhook_urls()

    def validate(self):
        self.validate_interval()
        self.setup_scheduled_job()

    def validate_interval(self):
        if (self.enable_stock_sync and self.stock_sync_interval > 60) or (
            self.enable_order_sync
            and self.order_sync_frequency == "Minutes"
            and self.order_sync_interval > 60
        ):
            frappe.throw(_("Interval in minutes cannot be greater than 60 minutes."))

    @frappe.whitelist()
    def generate_secret(self):
        woocommerce_setup = get_woocommerce_setup()
        woocommerce_setup.webhook_secret = frappe.generate_hash()
        woocommerce_setup.save()

    def set_webhook_urls(self):
        """Set the webhook URLs for the WooCommerce actions."""
        if self.webhook_endpoints:
            return

        try:
            url = frappe.request.url
        except Exception:
            url = "http://localhost:8000"

        server_url = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(url))
        path_to_webhooks = "/api/method/woocommerce_integration.webhooks"
        endpoints = [
            f"{action}: {server_url + path_to_webhooks + '.' + method}"
            for action, method in ACTION_MAP.items()
        ]

        self.webhook_endpoints = "\n".join(endpoints)

    def setup_scheduled_job(self):
        """Start/Stop/Edit a scheduled server script for stock/order sync."""
        self.create_update_job("stock")
        self.create_update_job("order")

    def create_update_job(self, job_type: str):
        """Create/update/enable/disable a scheduled server script for stock/order sync."""
        sync_switch = f"enable_{job_type}_sync"
        interval_field = f"{job_type}_sync_interval"
        script_field = f"{job_type}_server_script"
        frequency_field = f"{job_type}_sync_frequency" if job_type == "order" else None
        if script_name := self.get(script_field):
            # Update the existing script as per the new interval
            server_script = frappe.get_doc("Server Script", script_name)
        else:
            server_script = frappe.get_doc(
                dict(
                    doctype="Server Script",
                    __newname=f"Batch Sync WooCommerce {job_type.title()}",
                    script_type="Scheduler Event",
                    module="WooCommerce",
                    script=f"frappe.call('woocommerce_integration.woocommerce.sync_utils.batch_sync_{job_type}')",
                )
            )

        server_script.disabled = True #not self.get(sync_switch)
        frequency = self.get(frequency_field) if frequency_field else "Minutes"
        # Set the frequency in the script
        server_script.update(
            self.get_script_frequency(frequency, self.get(interval_field))
        )
        server_script.save()
        setattr(self, script_field, server_script.name)

    def disable_script(self, disabled: bool, script: str):
        """Enable or disable the scheduled job."""
        server_script = frappe.get_doc("Server Script", script)
        server_script.disabled = disabled
        server_script.save()

    def get_script_frequency(self, frequency: str, value: str) -> str:
        if frequency == "Minutes":
            return frappe._dict(
                event_frequency="Cron",
                cron_format=f"0/{value} * * * *",
            )
        return frappe._dict(event_frequency=frequency)


@frappe.whitelist()
def sync_stock() -> None:
    """Sync stock from ERPNext to WooCommerce."""
    frappe.enqueue(
        method="woocommerce_integration.woocommerce.sync_utils.batch_sync_stock",
        now=frappe.conf.developer_mode,
    )

    frappe.msgprint(
        _("Stock is being synced from ERPNext to WooCommerce in the background."),
        alert=True,
        indicator="blue",
    )


@frappe.whitelist()
def sync_orders() -> None:
    """Sync orders from WooCommerce to ERPNext."""
    frappe.enqueue(
        method="woocommerce_integration.woocommerce.sync_utils.batch_sync_order",
        now=frappe.conf.developer_mode,
        queue="long",
    )

    frappe.msgprint(
        _("Orders are being synced from WooCommerce to ERPNext in the background."),
        alert=True,
        indicator="blue",
    )

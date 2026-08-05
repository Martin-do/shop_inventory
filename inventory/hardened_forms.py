from django import forms

from .forms import ProductForm


class AuditedProductForm(ProductForm):
    """Require a reason whenever an existing product's total stock is changed."""

    def clean(self):
        cleaned = super().clean()
        if self.instance and self.instance.pk and "total_stock" in self.fields:
            requested_stock = cleaned.get("total_stock")
            current_stock = self.instance.stock_on_hand
            reason = (cleaned.get("adjustment_note") or "").strip()
            if requested_stock is not None and requested_stock != current_stock and len(reason) < 5:
                self.add_error(
                    "adjustment_note",
                    forms.ValidationError("Enter a clear stock-adjustment reason of at least 5 characters."),
                )
        return cleaned

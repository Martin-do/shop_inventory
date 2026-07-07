from decimal import Decimal

from django import forms

from .models import Category, Product


class ProductForm(forms.Form):
    name = forms.CharField(max_length=180, widget=forms.TextInput(attrs={"autofocus": True}))
    barcode = forms.CharField(max_length=80, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    category = forms.ModelChoiceField(queryset=Category.objects.all(), required=False)
    cost_price = forms.DecimalField(min_value=Decimal("0.00"), decimal_places=2, max_digits=12, required=False)
    selling_price = forms.DecimalField(min_value=Decimal("0.00"), decimal_places=2, max_digits=12)
    opening_stock = forms.IntegerField(min_value=0, initial=0, required=False)
    reorder_level = forms.IntegerField(min_value=0, initial=5)

    def clean_barcode(self):
        barcode = self.cleaned_data["barcode"].strip()
        if Product.objects.filter(barcode=barcode).exists():
            raise forms.ValidationError("A product with this barcode already exists.")
        return barcode

    def clean_name(self):
        return self.cleaned_data["name"].strip()


class ReceiveStockForm(forms.Form):
    barcode = forms.CharField(
        max_length=80,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "off", "inputmode": "numeric"}),
    )
    quantity = forms.IntegerField(min_value=1, widget=forms.NumberInput(attrs={"min": 1}))
    note = forms.CharField(
        max_length=240,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Supplier, invoice, or reason"}),
    )

    def clean_barcode(self):
        barcode = self.cleaned_data["barcode"].strip()
        if not Product.objects.filter(barcode=barcode, is_active=True).exists():
            raise forms.ValidationError("No active product was found for this barcode.")
        return barcode

    def clean_note(self):
        return self.cleaned_data.get("note", "").strip()


class AddToCartForm(forms.Form):
    barcode = forms.CharField(max_length=80)
    quantity = forms.IntegerField(min_value=1, initial=1)

    def clean_barcode(self):
        barcode = self.cleaned_data["barcode"].strip()
        if not Product.objects.filter(barcode=barcode, is_active=True).exists():
            raise forms.ValidationError("No active product was found for this barcode.")
        return barcode


class CheckoutForm(forms.Form):
    cashier_name = forms.CharField(max_length=120, required=False)
    amount_paid = forms.DecimalField(min_value=Decimal("0.00"), decimal_places=2, max_digits=12)

    def clean_cashier_name(self):
        return self.cleaned_data.get("cashier_name", "").strip()

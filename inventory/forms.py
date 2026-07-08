from decimal import Decimal

from django import forms

from .models import Category, Product


class ProductForm(forms.ModelForm):
    opening_stock = forms.IntegerField(min_value=0, initial=0, required=False)
    new_category = forms.CharField(
        max_length=120,
        required=False,
        help_text="Type a name here to create a new category (used if set).",
        widget=forms.TextInput(attrs={"placeholder": "Or type a new category", "autocomplete": "off"}),
    )

    class Meta:
        model = Product
        fields = ["name", "barcode", "category", "cost_price", "selling_price", "reorder_level", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"autofocus": True}),
            "barcode": forms.TextInput(attrs={"autocomplete": "off"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].required = False
        self.fields["category"].empty_label = "— Select a category —"
        self.fields["cost_price"].required = False
        if self.instance.pk:
            # Editing an existing product: opening stock only applies on create.
            self.fields.pop("opening_stock")
        else:
            # Creating: new products are active by default; no toggle needed yet.
            self.fields.pop("is_active")
        self.order_fields(["name", "barcode", "category", "new_category"])

    def clean_new_category(self):
        return self.cleaned_data.get("new_category", "").strip()

    def save(self, commit=True):
        new_category = self.cleaned_data.get("new_category")
        if new_category:
            category, _ = Category.objects.get_or_create(name=new_category)
            self.instance.category = category
        return super().save(commit=commit)

    def clean_barcode(self):
        return self.cleaned_data["barcode"].strip()

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_cost_price(self):
        return self.cleaned_data.get("cost_price") or Decimal("0.00")


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

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode

from . import payments
from .models import Customer, Order, OrderItem, Payment, Product, ShippingAddress


admin.site.site_header = 'Rento Administration'
admin.site.site_title = 'Rento Administration'
admin.site.index_title = 'Dashboard'


def _site_login(request, extra_context=None):
    # One login form for the whole site, with its failed-attempt lockout.
    next_url = request.GET.get('next') or reverse('admin:index')
    return redirect(f"{reverse('login')}?{urlencode({'next': next_url})}")


admin.site.login = _site_login


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer', 'status', 'get_cart_total', 'transaction_id', 'date_ordered')
    list_filter = ('status',)
    search_fields = ('id', 'transaction_id', 'customer__email', 'customer__name')
    inlines = [OrderItemInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('gateway_order_id', 'provider', 'order', 'status', 'amount', 'currency', 'gateway_payment_id', 'created_at')
    list_filter = ('status', 'provider')
    search_fields = ('gateway_order_id', 'gateway_payment_id')
    readonly_fields = [field.name for field in Payment._meta.fields]
    actions = ['recheck_with_gateway', 'confirm_reviewed', 'reject_reviewed']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # payment records are the audit trail

    def has_recheck_permission(self, request):
        return request.user.is_superuser

    @admin.action(description='Confirm reviewed payments as paid (found in my PayPal account)', permissions=['recheck'])
    def confirm_reviewed(self, request, queryset):
        reviewed = queryset.filter(status=Payment.Status.REVIEW)
        count = reviewed.count()
        for payment in reviewed:
            payments.mark_paid(payment, payment.gateway_payment_id)
        self.message_user(request, f'{count} payment(s) confirmed.')

    @admin.action(description='Reject reviewed payments (not found in my PayPal account)', permissions=['recheck'])
    def reject_reviewed(self, request, queryset):
        reviewed = queryset.filter(status=Payment.Status.REVIEW)
        count = reviewed.count()
        for payment in reviewed:
            payments.reject_review(payment)
        self.message_user(request, f'{count} payment(s) rejected.')

    @admin.action(description='Re-check selected payments with the gateway', permissions=['recheck'])
    def recheck_with_gateway(self, request, queryset):
        for payment in queryset:
            try:
                payments.sync(payment)
            except payments.PaymentError as exc:
                self.message_user(request, f'{payment.gateway_order_id}: {exc}', messages.ERROR)
        self.message_user(request, 'Re-check finished.')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'digital')
    search_fields = ('name',)


admin.site.register(Customer)
admin.site.register(OrderItem)
admin.site.register(ShippingAddress)

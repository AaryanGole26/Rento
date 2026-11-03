from django.urls import path

from . import views

urlpatterns = [
    path('', views.store, name='store'),
    path('cart/', views.cart, name='cart'),
    path('checkout/', views.checkout, name='checkout'),
    path('cart_data/', views.cart_data, name='cart_data'),
    path('update_item/', views.updateItem, name='update_item'),

    path('signup/', views.SignupPage, name='signup'),
    path('login/', views.LoginPage, name='login'),
    path('logout/', views.LogoutPage, name='logout'),
    path('forgot-password/', views.ForgotPasswordView.as_view(), name='forgot_password'),
    path('reset-password/<uidb64>/<token>/', views.ResetPasswordView.as_view(), name='password_reset_confirm'),

    path('payment/create/', views.create_payment, name='payment_create'),
    path('payment/verify/', views.verify_payment, name='payment_verify'),
    path('payment/failed/', views.payment_failed, name='payment_failed'),
    path('payment/webhook/', views.razorpay_webhook, name='payment_webhook'),
    path('order/<int:pk>/', views.order_detail, name='order_detail'),
]

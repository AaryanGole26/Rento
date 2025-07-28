from django.urls import path
from django.contrib import admin
from . import views

urlpatterns = [
    # Leave as empty string for base url
    path('', views.store, name="store"),
    path('logout/', views.LogoutPage, name='logout'),
    path('cart/', views.cart, name="cart"),
    path('checkout/', views.checkout, name="checkout"),
    path('cart_data/', views.cart_data, name='cart_data'),
    path('signup/', views.SignupPage, name="signup"),
    path('login/', views.LoginPage, name="login"),
    path('forgot-password/', views.ForgotPasswordPage, name='forgot_password'),
    path('reset-password/', views.ResetPasswordPage, name='reset_password'),
    path('update_item/', views.updateItem, name="update_item"),
    path('process_order/', views.processOrder, name="process_order"),
    path('paymentgateway/', views.payment_gateway, name='paymentgateway'),
]

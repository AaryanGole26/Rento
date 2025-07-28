from django.shortcuts import render, HttpResponse, redirect
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.template import RequestContext
from django.utils.crypto import get_random_string
import json
import random
import time
import datetime
from .models import *
from .utils import cookieCart, cartData, guestOrder

def LogoutPage(request):
    logout(request)
    return redirect('store')

def SignupPage(request):

    if request.method == 'POST':
        uname = request.POST.get('username')
        email = request.POST.get('email')
        pass1 = request.POST.get('password1')
        pass2 = request.POST.get('password2')

        if uname == "":
            return HttpResponse("Your username cannot be empty!")
        if email == "":
            return HttpResponse("Your email cannot be empty!")
        if pass1 == "":
            return HttpResponse("Your password cannot be empty!")
        if pass2 == "":
            return HttpResponse("Enter password again for confirming!")
        if pass1 != pass2:
            return HttpResponse("Your password and confirmed password are not matching!")
        else:
            my_user = User.objects.create_user(uname, email, pass1)
            my_user.save()
            return redirect('login')

    return render(request, 'store/signup.html')

def LoginPage(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        pass1 = request.POST.get('pass')
        user = authenticate(request, username=username, password=pass1)
        if user is not None:
            login(request, user)
            print("Login successful:", request.user)  # Add this
            return redirect('store')
        else:
            return HttpResponse("Username or Password is incorrect!")

    return render(request, 'store/login.html')

def store(request):
    print("User:", request.user)
    print("Is authenticated:", request.user.is_authenticated)
    data = cartData(request)
    cartItems = data['cartItems']
    order = data['order']
    items = data['items']
    products = Product.objects.all()
    context = {'products': products, 'cartItems': cartItems}
    return render(request, 'store/store.html', context)

@login_required(login_url='login')

def cart(request):
    data = cartData(request)

    cartItems = data['cartItems']
    order = data['order']
    items = data['items']

    context = {'items': items, 'order': order, 'cartItems': cartItems}
    return render(request, 'store/cart.html', context)


def checkout(request):
    data = cartData(request)

    cartItems = data['cartItems']
    order = data['order']
    items = data['items']

    context = {'items': items, 'order': order, 'cartItems': cartItems}
    return render(request, 'store/checkout.html', context)

def payment_gateway(request):
    return render(request, 'store/paymentgateway.html')


def updateItem(request):
    data = json.loads(request.body)
    productId = data['productId']
    action = data['action']
    print('Action:', action)
    print('Product:', productId)

    try:
        customer = request.user.customer
    except Customer.DoesNotExist:
        customer = Customer.objects.create(user=request.user)

    product = Product.objects.get(id=productId)
    order, created = Order.objects.get_or_create(customer=customer, complete=False)

    orderItem, created = OrderItem.objects.get_or_create(order=order, product=product)

    if action == 'add':
        orderItem.quantity = (orderItem.quantity + 1)
    elif action == 'remove':
        orderItem.quantity = (orderItem.quantity - 1)

    orderItem.save()

    if orderItem.quantity <= 0:
        orderItem.delete()

    return JsonResponse('Item was added', safe=False)


def processOrder(request):
    transaction_id = datetime.datetime.now().timestamp()
    data = json.loads(request.body)

    if request.user.is_authenticated:
        customer = request.user.customer
        order, created = Order.objects.get_or_create(customer=customer, complete=False)
    else:
        customer, order = guestOrder(request, data)

    total = float(data['form']['total'])
    order.transaction_id = transaction_id

    if total == order.get_cart_total:
        order.complete = True
    order.save()

    if order.shipping == True:
        ShippingAddress.objects.create(
            customer=customer,
            order=order,
            address=data['shipping']['address'],
            city=data['shipping']['city'],
            state=data['shipping']['state'],
            zipcode=data['shipping']['zipcode'],
        )

    return JsonResponse('Payment submitted..', safe=False)


def cart_data(request):
    if request.user.is_authenticated:
        customer = request.user.customer
        order, created = Order.objects.get_or_create(customer=customer, complete=False)
        cart_items = order.get_cart_items
    else:
        data = cookieCart(request)
        cart_items = data['cartItems']

    return JsonResponse({'cartItems': cart_items})

def ForgotPasswordPage(request):
    if request.method == 'POST':
        email = request.POST['email']
        try:
            user = User.objects.get(email=email)
            code = str(random.randint(100000, 999999))
            request.session['reset_code'] = code
            request.session['reset_email'] = email

            send_mail(
                subject="Your Password Reset Code",
                message=f"Your password reset code is: {code}",
                from_email='your_email@gmail.com',
                recipient_list=[email],
                fail_silently=False,
            )
            return redirect('reset_password')
        except User.DoesNotExist:
            messages.error(request, "No user with that email exists.")
            return redirect('forgot_password')

    return render(request, 'store/forgot_password.html')


def ResetPasswordPage(request):
    if request.method == 'POST':
        code = request.POST['code']
        new_password = request.POST['new_password']
        confirm_password = request.POST['confirm_password']
        session_code = request.session.get('reset_code')
        email = request.session.get('reset_email')

        if code != session_code:
            messages.error(request, "Invalid code.")
            return redirect('reset_password')

        if new_password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return redirect('reset_password')

        try:
            user = User.objects.get(email=email)
            user.set_password(new_password)
            user.save()
            messages.success(request, "Password reset successful. Please log in.")
            return redirect('login')
        except:
            messages.error(request, "Something went wrong.")
            return redirect('reset_password')

    return render(request, 'store/reset_password.html')

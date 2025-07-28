const updateBtns = document.querySelectorAll('.update-cart');

updateBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    const productId = btn.dataset.product;
    const action = btn.dataset.action;
    console.log('Product ID:', productId, 'Action:', action);
    console.log('USER:', user);

    if (user === 'AnonymousUser') {
      handleCartWithCookies(productId, action);
    } else {
      sendCartUpdateToServer(productId, action);
    }
  });
});

function sendCartUpdateToServer(productId, action) {
  console.log('Authenticated user, updating server cart...');
  fetch('/update_item/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrftoken,
    },
    body: JSON.stringify({ productId, action }),
  })
    .then(res => res.json())
    .then(data => {
      // Fetch new cart count after update
      fetch('/cart_data/')
        .then(res => res.json())
        .then(data => {
          document.getElementById('cart-total').innerText = data.cartItems;
        });
    });
}

function handleCartWithCookies(productId, action) {
  console.log('Anonymous user, modifying cart in cookies...');

  if (action === 'add') {
    if (!cart[productId]) {
      cart[productId] = { quantity: 1 };
    } else {
      cart[productId].quantity += 1;
    }
  }

  if (action === 'remove') {
    cart[productId].quantity -= 1;
    if (cart[productId].quantity <= 0) {
      delete cart[productId];
    }
  }

  console.log('Updated CART:', cart);
  document.cookie = 'cart=' + JSON.stringify(cart) + ';domain=;path=/';
  updateCartBadge();
}

function updateCartBadge() {
  const totalItems = Object.values(cart).reduce((sum, item) => sum + item.quantity, 0);
  const badge = document.getElementById('cart-total');
  if (badge) {
    badge.innerText = totalItems;
  }
} 

// Ensure cart badge is always correct on load
window.addEventListener('DOMContentLoaded', updateCartBadge);

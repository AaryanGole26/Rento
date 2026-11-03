// Delegated so the controls keep working after the cart area is refreshed in place.
document.addEventListener('click', function (event) {
  var button = event.target.closest && event.target.closest('.update-cart');
  if (!button) {
    return;
  }
  var change = {
    productId: button.dataset.product,
    action: button.dataset.action,
    refresh: button.dataset.reload === 'true',
    name: button.dataset.name,
  };
  if (isAuthenticated) {
    updateServerCart(change);
  } else {
    updateCookieCart(change);
  }
});

function setCartBadge(count) {
  var badge = document.getElementById('cart-total');
  if (!badge) {
    return;
  }
  badge.innerText = count;
  badge.classList.remove('bump');
  void badge.offsetWidth; // restarts the animation
  badge.classList.add('bump');
}

function afterCartChange(change, count) {
  setCartBadge(count);
  if (change.refresh) {
    refreshCartArea();
  } else if (change.action === 'add' && window.rentoToast) {
    window.rentoToast((change.name || 'Item') + ' added to your cart', 'View cart', '/cart/');
  }
}

// Updates the cart without a full page reload, falling back to one if anything goes wrong.
function refreshCartArea() {
  var area = document.getElementById('cart-content');
  if (!area) {
    location.reload();
    return;
  }
  area.classList.add('refreshing');
  fetch(location.href, { credentials: 'same-origin' })
    .then(function (response) {
      if (!response.ok) {
        throw new Error('Refresh failed: ' + response.status);
      }
      return response.text();
    })
    .then(function (html) {
      var fresh = new DOMParser().parseFromString(html, 'text/html').getElementById('cart-content');
      if (!fresh) {
        throw new Error('Cart area missing');
      }
      area.innerHTML = fresh.innerHTML;
      area.classList.remove('refreshing', 'refreshed');
      void area.offsetWidth;
      area.classList.add('refreshed');
    })
    .catch(function () {
      location.reload();
    });
}

function updateServerCart(change) {
  fetch('/update_item/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrftoken },
    body: JSON.stringify({ productId: change.productId, action: change.action }),
  })
    .then(function (response) {
      if (!response.ok) {
        throw new Error('Cart update failed: ' + response.status);
      }
      return response.json();
    })
    .then(function (data) {
      afterCartChange(change, data.cartItems);
    })
    .catch(function (error) {
      console.error(error);
      if (window.rentoToast) {
        window.rentoToast('Could not update your cart. Please try again.');
      }
    });
}

function updateCookieCart(change) {
  var productId = change.productId;
  if (change.action === 'add') {
    cart[productId] = { quantity: (cart[productId] ? cart[productId].quantity : 0) + 1 };
  } else if (change.action === 'remove' && cart[productId]) {
    cart[productId].quantity -= 1;
    if (cart[productId].quantity <= 0) {
      delete cart[productId];
    }
  }

  document.cookie = 'cart=' + JSON.stringify(cart) + ';path=/;max-age=2592000;SameSite=Lax';
  var count = Object.values(cart).reduce(function (sum, item) { return sum + item.quantity; }, 0);
  afterCartChange(change, count);
}

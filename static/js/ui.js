/* Interface behaviour shared by every page. */
(function () {
  var root = document.documentElement;

  // Light follows the pointer across glass, updated at most once per frame
  if (window.matchMedia('(hover: hover)').matches) {
    var pending = null;
    document.addEventListener('pointermove', function (event) {
      var glass = event.target.closest && event.target.closest('.surface');
      if (!glass) {
        return;
      }
      if (!pending) {
        requestAnimationFrame(function () {
          var box = pending.glass.getBoundingClientRect();
          pending.glass.style.setProperty('--mx', (pending.x - box.left) + 'px');
          pending.glass.style.setProperty('--my', (pending.y - box.top) + 'px');
          pending = null;
        });
      }
      pending = { glass: glass, x: event.clientX, y: event.clientY };
    }, { passive: true });
  }

  // Mobile menu
  var toggler = document.querySelector('.navbar-toggler');
  var menu = document.getElementById('navbarContent');
  if (toggler && menu) {
    var setOpen = function (open) {
      menu.classList.toggle('show', open);
      toggler.setAttribute('aria-expanded', String(open));
    };
    toggler.addEventListener('click', function () {
      setOpen(!menu.classList.contains('show'));
    });
    menu.addEventListener('click', function (event) {
      if (event.target.closest('a')) {
        setOpen(false);
      }
    });
  }

  // Theme switch
  var themeToggle = document.getElementById('theme-toggle');
  if (themeToggle) {
    themeToggle.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try {
        localStorage.setItem('rento-theme', next);
      } catch (error) {
        // The choice just won't be remembered.
      }
    });
  }

  // A thin bar shows that the next page is on its way
  var progress = document.getElementById('nav-progress');

  function startProgress() {
    if (progress) {
      progress.classList.add('active');
    }
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0
        || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
        || link.target === '_blank' || link.origin !== location.origin) {
      return;
    }
    if (link.pathname === location.pathname && link.search === location.search && link.hash) {
      return; // same-page anchor
    }
    startProgress();
  });

  // Forms show a spinner while they submit
  document.addEventListener('submit', function (event) {
    if (event.defaultPrevented || event.target.hasAttribute('data-no-loader')) {
      return;
    }
    var button = event.target.querySelector('button[type="submit"]');
    if (button && button.classList.contains('btn')) {
      button.classList.add('is-loading');
    }
    startProgress();
  });

  // Coming back through browser history restores a page in its previous state
  window.addEventListener('pageshow', function (event) {
    if (event.persisted) {
      location.reload(); // a page restored from memory may show a logged-out session as logged in
      return;
    }
    if (progress) {
      progress.classList.remove('active');
    }
    document.querySelectorAll('.btn.is-loading[type="submit"]').forEach(function (button) {
      button.classList.remove('is-loading');
    });
  });

  // Small confirmation messages
  window.rentoToast = function (text, linkText, linkHref) {
    var region = document.getElementById('toast-region');
    if (!region) {
      return;
    }
    var toast = document.createElement('div');
    toast.className = 'surface toast';
    var message = document.createElement('span');
    message.textContent = text;
    toast.appendChild(message);
    if (linkText) {
      var link = document.createElement('a');
      link.textContent = linkText;
      link.href = linkHref;
      toast.appendChild(link);
    }
    region.appendChild(toast);
    while (region.children.length > 2) {
      region.removeChild(region.firstChild);
    }
    setTimeout(function () { toast.remove(); }, 2800);
  };
})();

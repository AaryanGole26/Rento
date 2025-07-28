<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" class="logo" width="120"/>

# [AaryanGole26/Rento](https://github.com/AaryanGole26/Rento)

Rento - Engineer's Rental Website
[](https://github.com/AaryanGole26/Rento#rento---engineers-rental-website)
Mini Project SE IIIrd Sem, AY 2023-24

make changes to readme.md

Certainly! Below is a polished and informative README.md file for your "Rento - Engineer's Rental Website" project. It includes sections such as project description, features, installation instructions, usage, and notes relevant to your Django-based rental app.

```markdown
# Rento - Engineer's Rental Website

Mini Project SE IIIrd Sem, AY 2023-24

---

## Project Overview

**Rento** is a web-based application designed to facilitate the rental of engineering tools and equipment. This platform enables users (customers) to browse, select, and rent various engineering equipment easily. It is built using Django 5.2.4 and Python 3.12.

The project serves as an academic mini-project demonstrating core concepts such as user authentication, model relationships, e-commerce style cart & order management, and session handling.

---

## Features

- User registration and authentication
- Customer and user profile management
- Browse available engineering equipment/tools
- Add items to cart and manage cart contents
- Place rental orders and track order status
- Handle guest user sessions with cookie-based carts
- Admin panel to manage products, orders, and users

---

## Technologies Used

- Python 3.12.7
- Django 5.2.4
- SQLite (default Django database)
- HTML, CSS, JavaScript for frontend
- Bootstrap (optional) for styling

---

## Installation

1. **Clone the repository**

```

git clone https://github.com/AaryanGole26/Rento.git
cd Rento

```

2. **Create and activate a virtual environment**

```

python -m venv venv

# Windows

venv\Scripts\activate

# macOS/Linux

source venv/bin/activate

```

3. **Install dependencies**

```

pip install -r requirements.txt

```

4. **Run migrations**

```

python manage.py migrate

```

5. **Create a superuser (optional but recommended for admin access)**

```

python manage.py createsuperuser

```

6. **Run the development server**

```

python manage.py runserver

```

7. **Open your browser and navigate to**

```

http://127.0.0.1:8000/

```

---

## Usage

- Register as a new user or login if you already have an account.
- Browse the available rental items.
- Add equipment to your cart.
- Proceed to checkout and place your rental order.
- Admin users can manage products and orders via the Django admin interface at `/admin/`.

---

## Notes

- The app handles signed-in users and guests separately, with cart data stored in cookies for guests.
- The project currently uses SQLite, but can be configured to use other databases.
- For any `RelatedObjectDoesNotExist` errors related to `User` and `Customer` relationships, ensure customers are created correctly for users.

---

## Contributing

This project is currently for academic purposes. Feel free to fork and modify it for learning or personal use.

---

## License

This project is provided as-is for educational use.



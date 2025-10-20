import requests
from bs4 import BeautifulSoup
import os
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
import schedule
import time
import smtplib
import ssl
import threading

basedir = os.path.abspath(os.path.dirname(__file__))

if 'RENDER' in os.environ:
    db_path = os.path.join('/data', 'database.db')
else:
    instance_path = os.path.join(basedir, 'instance')
    if not os.path.exists(instance_path):
        os.makedirs(instance_path)
    db_path = os.path.join(instance_path, 'database.db')

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), nullable=False, unique=True)
    title = db.Column(db.String(500), nullable=False)
    price = db.Column(db.Float, nullable=False)
    subscriptions = db.relationship('Subscription', backref='product', lazy=True, cascade="all, delete-orphan")


class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(120), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('user_email', 'product_id', name='_user_product_uc'),)


headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
}


def send_notification_email(receiver_email, product, new_price):
    sender_email = os.environ.get('SENDER_EMAIL')
    password = os.environ.get('SENDER_PASSWORD')

    if not all([sender_email, password]):
        print("Sender email credentials are not set.")
        return

    port = 465
    smtp_server = "smtp.gmail.com"

    subject = f"Subject: Price Drop Alert: {product.title[:30]}..."
    body = f"""
Hello,

A price drop has been detected for a product you are tracking!

Product: {product.title}

Old Price: {product.price} EUR
NEW PRICE: {new_price} EUR

You can check it out here:
{product.url}

Sincerely,
DealRelay
"""
    message = f"{subject}\n\n{body}".encode('utf-8')
    context = ssl.create_default_context()

    print(f"Attempting to send email to {receiver_email}...")
    try:
        with smtplib.SMTP_SSL(smtp_server, port, context=context) as server:
            server.login(sender_email, password)
            server.sendmail(sender_email, receiver_email, message)
            print(f"Email successfully sent to {receiver_email}!")
    except Exception as e:
        print(f"An error occurred while sending email: {e}")


def get_product_details(url_to_check):
    print(f"Checking URL: {url_to_check[:50]}...")
    try:
        page = requests.get(url_to_check, headers=headers)
        page.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Connection error: {e}")
        return "N/A", 0.0

    soup = BeautifulSoup(page.content, "html.parser")

    try:
        product_name = soup.find(id="productTitle").get_text(strip=True)
    except AttributeError:
        product_name = "N/A"

    try:
        price_whole_str = soup.find(class_="a-price-whole").get_text(strip=True).replace('.', '').replace(',', '')
        price_fraction_str = soup.find(class_="a-price-fraction").get_text(strip=True)
        full_price = float(f"{price_whole_str}.{price_fraction_str}")
    except (AttributeError, ValueError):
        full_price = 0.0

    return product_name, full_price


def check_prices():
    print("Hourly price check started.")
    products = Product.query.all()

    if not products:
        print("No products found in the database.")
        return

    for product in products:
        print(f"Checking: {product.title[:30]}...")
        _, new_price = get_product_details(product.url)

        if 0 < new_price < product.price:
            print(f"PRICE DROP DETECTED! {product.title[:30]}")
            print(f"Old Price: {product.price} EUR → New Price: {new_price} EUR")

            for sub in product.subscriptions:
                send_notification_email(sub.user_email, product, new_price)

            product.price = new_price
            db.session.commit()
            print("Database updated.")
        else:
            print("No price change detected.")

    print("Price check completed.")


def job():
    with app.app_context():
        check_prices()


def run_scheduler():
    print("Background scheduler started. Prices will be checked every hour.")
    schedule.every().hour.do(job)
    while True:
        schedule.run_pending()
        time.sleep(1)


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/track', methods=['POST'])
def track_product():
    data = request.get_json()
    if not data or 'product_url' not in data or 'user_email' not in data:
        return jsonify({'message': 'Missing product_url or user_email.'}), 400

    product_url = data['product_url']
    user_email = data['user_email']

    product = Product.query.filter_by(url=product_url).first()

    if not product:
        print(f"New product detected: {product_url}. Fetching details...")
        title, price = get_product_details(product_url)

        if title == "N/A" or price == 0.0:
            return jsonify({'message': 'Failed to retrieve product information. Please verify the Amazon URL.'}), 400

        product = Product(url=product_url, title=title, price=price)
        db.session.add(product)
        db.session.flush()

    subscription = Subscription.query.filter_by(user_email=user_email, product_id=product.id).first()

    if subscription:
        return jsonify({'message': 'You are already tracking this product.'}), 200

    new_subscription = Subscription(user_email=user_email, product_id=product.id)
    db.session.add(new_subscription)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Database error: {e}")
        return jsonify({'message': 'An error occurred while creating the tracking request.'}), 500

    return jsonify({'message': f'Success! Tracking has been set up for "{product.title[:30]}..."'}), 201


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()

    app.run(debug=True, use_reloader=False)

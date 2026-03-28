import hashlib
import sqlite3
from flask import Flask, jsonify, request, send_from_directory, g, render_template, session
from flask_cors import CORS
from pymongo import MongoClient
import os

BASE_DB = 'database.db'
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)
db = client['shopos']
users_collection = db['users']
wishlists_collection = db['wishlists']

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = 'your_secret_key_here'  # Change this to a secure key
CORS(app)

# DB helper

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(BASE_DB)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext

def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(query, args=()):
    db = get_db()
    cur = db.execute(query, args)
    db.commit()
    return cur.lastrowid


def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def init_db():
    db = sqlite3.connect(BASE_DB)
    c = db.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, category TEXT, price REAL, stock INTEGER, icon TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, total REAL, status TEXT, created_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS order_items (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, product_name TEXT, qty INTEGER, unit_price REAL, FOREIGN KEY(order_id) REFERENCES orders(id))''')

    # Seed if empty
    if not query_db('SELECT id FROM products LIMIT 1'):
        products = [
            ('Basmati Rice 5kg', 'Grocery', 450, 20, '🌾'),
            ('Toor Dal 1kg', 'Grocery', 120, 35, '🫘'),
            ('Amul Butter 500g', 'Dairy', 280, 3, '🧈'),
            ('Bisleri Water 1L', 'Beverages', 20, 100, '💧'),
            ("Lay's Chips", 'Snacks', 30, 50, '🥔'),
            ('Colgate Toothpaste', 'Grocery', 95, 2, '🪥'),
            ('Notebook A4', 'Stationery', 60, 40, '📒'),
            ('Maggi Noodles', 'Snacks', 14, 80, '🍜'),
            ('Tide Detergent 1kg', 'Grocery', 185, 0, '🧴'),
            ('Parle-G Biscuits', 'Snacks', 10, 120, '🍪'),
            ('Sunflower Oil 1L', 'Grocery', 195, 15, '🫙'),
            ('Green Tea 25 bags', 'Beverages', 110, 25, '🍵'),
        ]
        c.executemany('INSERT INTO products (name, category, price, stock, icon) VALUES (?, ?, ?, ?, ?)', products)

    db.commit()
    db.close()

    # Seed users in MongoDB if empty
    if users_collection.count_documents({}) == 0:
        users = [
            {'username': 'user', 'full_name': 'Ravi Kumar', 'password': hash_password('user123'), 'role': 'customer'},
            {'username': 'admin', 'full_name': 'Admin', 'password': hash_password('admin123'), 'role': 'admin'}
        ]
        users_collection.insert_many(users)


@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    requested_role = data.get('role', '').strip()

    if not username or not password:
        return jsonify({'error': 'Missing credentials'}), 400

    user = users_collection.find_one({'username': username})
    if not user or user['password'] != hash_password(password):
        return jsonify({'error': 'Invalid username/password'}), 401

    # If role is provided in request, validate it; fallback to user role in DB
    if requested_role and user['role'] != requested_role:
        return jsonify({'error': 'Please select the correct role for this user'}), 401

    # Store in session for auto-login
    session['user_id'] = str(user['_id'])
    session['username'] = user['username']
    session['full_name'] = user['full_name']
    session['role'] = user['role']

    return jsonify({'id': str(user['_id']), 'username': user['username'], 'full_name': user['full_name'], 'role': user['role']})

@app.route('/api/products', methods=['GET'])
def list_products():
    rows = query_db('SELECT * FROM products ORDER BY id')
    products = [dict(x) for x in rows]
    return jsonify(products)

@app.route('/api/wishlist', methods=['GET'])
def get_wishlist():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not logged in'}), 401
    wishlist = wishlists_collection.find_one({'user_id': user_id})
    if not wishlist:
        return jsonify([])
    return jsonify(wishlist.get('products', []))

@app.route('/api/wishlist', methods=['POST'])
def add_to_wishlist():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.json or {}
    product_id = data.get('product_id')
    if not product_id:
        return jsonify({'error': 'Missing product_id'}), 400
    wishlists_collection.update_one(
        {'user_id': user_id},
        {'$addToSet': {'products': product_id}},
        upsert=True
    )
    return jsonify({'message': 'Added to wishlist'})

@app.route('/api/wishlist/<int:product_id>', methods=['DELETE'])
def remove_from_wishlist(product_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not logged in'}), 401
    wishlists_collection.update_one(
        {'user_id': user_id},
        {'$pull': {'products': product_id}}
    )
    return jsonify({'message': 'Removed from wishlist'})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'message': 'Logged out'})

@app.route('/api/admin/products', methods=['POST'])
def add_product():
    payload = request.json or {}
    name = payload.get('name', '').strip()
    category = payload.get('category', '').strip()
    price = float(payload.get('price', 0))
    stock = int(payload.get('stock', 0))
    icon = payload.get('icon', '📦')

    if not name or price <= 0 or stock < 0:
        return jsonify({'error': 'Invalid product data'}), 400

    product_id = execute_db('INSERT INTO products (name, category, price, stock, icon) VALUES (?, ?, ?, ?, ?)',
                             (name, category, price, stock, icon))
    return jsonify({'id': product_id, 'name': name, 'category': category, 'price': price, 'stock': stock, 'icon': icon})

@app.route('/api/admin/products/<int:pid>', methods=['PUT'])
def update_product(pid):
    payload = request.json or {}
    name = payload.get('name', '').strip()
    category = payload.get('category', '').strip()
    price = float(payload.get('price', 0))
    stock = int(payload.get('stock', 0))
    icon = payload.get('icon', '📦')

    if not name or price <= 0 or stock < 0:
        return jsonify({'error': 'Invalid product data'}), 400

    execute_db('UPDATE products SET name=?, category=?, price=?, stock=?, icon=? WHERE id=?',
               (name, category, price, stock, icon, pid))
    return jsonify({'id': pid})

@app.route('/api/admin/products/<int:pid>', methods=['DELETE'])
def delete_product(pid):
    execute_db('DELETE FROM products WHERE id=?', (pid,))
    return jsonify({'id': pid})

@app.route('/api/orders', methods=['GET'])
def get_orders():
    role = session.get('role', 'customer')
    user_id = session.get('user_id')
    sort_by = request.args.get('sort_by', 'created_at')
    sort_dir = request.args.get('sort_dir', 'desc').lower()
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'

    sql = 'SELECT o.id, o.user_id, u.username, u.full_name as customer, o.total, o.status, o.created_at FROM orders o JOIN users u ON o.user_id=u.id'
    params = []

    if role == 'customer' and user_id:
        sql += ' WHERE o.user_id = ?'
        params.append(user_id)

    if sort_by == 'customer':
        sql += f' ORDER BY u.username {sort_dir.upper()}'
    elif sort_by == 'total':
        sql += f' ORDER BY o.total {sort_dir.upper()}'
    elif sort_by == 'status':
        sql += f' ORDER BY o.status {sort_dir.upper()}'
    else:
        sql += f' ORDER BY o.created_at {sort_dir.upper()}'

    rows = query_db(sql, params)
    orders = [dict(x) for x in rows]

    for order in orders:
        items = query_db('SELECT product_name, qty, unit_price FROM order_items WHERE order_id=?', (order['id'],))
        order['items'] = [dict(i) for i in items]

    return jsonify(orders)

@app.route('/api/orders', methods=['POST'])
def place_order():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not logged in'}), 401
    payload = request.json or {}
    items = payload.get('items', [])

    if not items:
        return jsonify({'error': 'Missing order info'}), 400

    total = 0
    for item in items:
        total += float(item['unit_price']) * int(item['qty'])

    cursor = get_db().cursor()
    cursor.execute('INSERT INTO orders (user_id, total, status, created_at) VALUES (?, ?, ?, datetime("now"))',
                   (user_id, total, 'completed'))
    order_id = cursor.lastrowid
    for item in items:
        cursor.execute('INSERT INTO order_items (order_id, product_name, qty, unit_price) VALUES (?, ?, ?, ?)',
                       (order_id, item['product_name'], item['qty'], item['unit_price']))
        cursor.execute('UPDATE products SET stock = stock - ? WHERE name = ?', (item['qty'], item['product_name']))

    get_db().commit()
    return jsonify({'order_id': order_id, 'total': total})

@app.route('/api/dashboard', methods=['GET'])
def dashboard():
    total_revenue = query_db('SELECT COALESCE(SUM(total),0) as total FROM orders', one=True)['total']
    total_products = query_db('SELECT COUNT(*) as cnt FROM products', one=True)['cnt']
    total_orders = query_db('SELECT COUNT(*) as cnt FROM orders', one=True)['cnt']
    low_stock = query_db('SELECT COUNT(*) as cnt FROM products WHERE stock <= 5', one=True)['cnt']
    return jsonify({'total_revenue': total_revenue, 'total_products': total_products, 'total_orders': total_orders, 'low_stock': low_stock})

@app.route('/api/low-stock', methods=['GET'])
def low_stock():
    rows = query_db('SELECT * FROM products WHERE stock <= 5 ORDER BY stock ASC')
    return jsonify([dict(x) for x in rows])

@app.route('/invoice/<int:order_id>')
def invoice(order_id):
    order_row = query_db('''
        SELECT o.id, o.user_id, u.username, u.full_name, o.total, o.status, o.created_at
        FROM orders o
        JOIN users u ON o.user_id = u.id
        WHERE o.id = ?''', (order_id,), one=True)
    if not order_row:
        return "Order not found", 404

    order = dict(order_row)
    items_rows = query_db('SELECT product_name, qty, unit_price FROM order_items WHERE order_id = ?', (order_id,))
    items = [dict(i) for i in items_rows]
    order['items'] = items

    return render_template('invoice.html', order=order, items=items)

if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)

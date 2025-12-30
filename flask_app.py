# This is a complete web application for managing rental rooms using Flask.
# To run this application:
# 1. Install required packages (if running locally): pip install flask flask-sqlalchemy flask-login werkzeug
# 2. Save this code to a file, e.g., app.py
# 3. Run: python app.py
# 4. Access via browser: http://127.0.0.1:5000/
# Default admin login: username='admin', password='admin'

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'  # Change this to a secure key
# Dùng Postgres trên Render, fallback SQLite khi chạy local
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///rental.db').replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Models
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # 'admin', 'user', 'tenant'
    
    # Thêm trường gán với khách thuê (chỉ dùng khi role = 'tenant')
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)
class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    rent_price = db.Column(db.Float, nullable=False)
    internet_fee = db.Column(db.Float, nullable=False, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Tenant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(150))
    room_id = db.Column(db.Integer, db.ForeignKey('room.id'), nullable=False)

class Contract(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    duration_months = db.Column(db.Integer, nullable=False)
    end_date = db.Column(db.Date)
    is_extended = db.Column(db.Boolean, default=False)

class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey('contract.id'), nullable=False)
    month = db.Column(db.Date, nullable=False)
    
    # Chỉ số điện
    electricity_old = db.Column(db.Float, default=0.0)
    electricity_new = db.Column(db.Float, default=0.0)
    electricity_price = db.Column(db.Float, nullable=False, default=4000.0)
    
    # Chỉ số nước
    water_old = db.Column(db.Float, default=0.0)
    water_new = db.Column(db.Float, default=0.0)
    
    # Tính tự động
    electricity_usage = db.Column(db.Float, default=0.0)
    water_usage = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, nullable=False)
    paid = db.Column(db.Boolean, default=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Helper functions
def calculate_water_cost(usage):
    if usage <= 5:
        return usage * 16000
    else:
        return (5 * 16000) + ((usage - 5) * 27000)

def calculate_bill(contract, electricity_old, electricity_new, water_old, water_new, electricity_price=4000.0):
    room = Room.query.get(Tenant.query.get(contract.tenant_id).room_id)
    
    # Tính số dùng
    electricity_usage = electricity_new - electricity_old if electricity_new >= electricity_old else 0
    water_usage = water_new - water_old if water_new >= water_old else 0
    
    # Tính tiền điện
    electricity_cost = electricity_usage * electricity_price
    
    # Tính tiền nước
    water_cost = calculate_water_cost(water_usage)
    
    # Tổng
    total = room.rent_price + room.internet_fee + electricity_cost + water_cost
    return total, electricity_usage, water_usage, electricity_cost, water_cost

# Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if current_user.role != 'admin':
        flash('Chỉ admin mới được tạo người dùng', 'danger')
        return redirect(url_for('dashboard'))
    
    # Lấy danh sách tất cả khách thuê để gán (nếu role tenant)
    tenants = Tenant.query.all()
    for tenant in tenants:
        tenant.room = Room.query.get(tenant.room_id)
            
        if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        role = request.form['role']
        
        # Kiểm tra username trùng
        if User.query.filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại', 'danger')
            return render_template('register.html', tenants=tenants)
        
        if not password:
            flash('Vui lòng nhập mật khẩu', 'danger')
            return render_template('register.html', tenants=tenants)
        
        new_user = User(
            username=username,
            password=generate_password_hash(password, method='pbkdf2:sha256'),
            role=role
        )
        
        # Nếu là khách thuê, gán tenant_id để liên kết với khách + phòng
        if role == 'tenant':
            tenant_id = request.form.get('tenant_id')
            if not tenant_id:
                flash('Vui lòng chọn khách thuê để gán', 'danger')
                return render_template('register.html', tenants=tenants)
            # Thêm cột tenant_linked_id vào model User (xem Bước 3 nếu chưa có)
            new_user.tenant_linked_id = int(tenant_id)
        
        db.session.add(new_user)
        db.session.commit()
        flash('Tài khoản đã được tạo thành công!', 'success')
        return redirect(url_for('manage_users'))
        
    return render_template('register.html', tenants=tenants)
    
@app.route('/manage_users')
@login_required
def manage_users():
    if current_user.role != 'admin':
        flash('Chỉ admin mới được truy cập trang này', 'danger')
        return redirect(url_for('dashboard'))
    
    users = User.query.all()
    total_admins = User.query.filter_by(role='admin').count()
    
    return render_template('manage_users.html', users=users, total_admins=total_admins)

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    if current_user.role != 'admin':
        flash('Chỉ admin mới được chỉnh sửa', 'danger')
        return redirect(url_for('dashboard'))
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role')
        
        # Kiểm tra username không trùng với user khác
        if username and username != user.username:
            existing = User.query.filter_by(username=username).first()
            if existing:
                flash('Tên đăng nhập đã được sử dụng', 'danger')
                return render_template('edit_users.html', user=user)
        
        user.username = username or user.username
        
        # Chỉ cập nhật password nếu người dùng nhập mới
        if password:
            user.password = generate_password_hash(password, method='pbkdf2:sha256')
        
        # Chỉ admin mới được thay đổi role của chính mình
        if role and role in ['admin', 'user', 'tenant']:
            if user.role == 'admin' and current_user.id == user.id and role != 'admin':
                flash('Không thể tự hạ quyền admin của chính mình', 'danger')
            else:
                user.role = role
        
        db.session.commit()
        flash('Người dùng đã được cập nhật thành công!', 'success')
        return redirect(url_for('manage_users'))
    
    return render_template('edit_users.html', user=user)

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if current_user.role != 'admin':
        flash('Chỉ admin mới được xóa', 'danger')
        return redirect(url_for('dashboard'))
    
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('Không thể xóa chính tài khoản đang đăng nhập', 'danger')
        return redirect(url_for('manage_users'))
    if user.role == 'admin' and User.query.filter_by(role='admin').count() == 1:
        flash('Không thể xóa admin cuối cùng', 'danger')
        return redirect(url_for('manage_users'))
    
    db.session.delete(user)
    db.session.commit()
    flash('Người dùng đã được xóa thành công', 'success')
    return redirect(url_for('manage_users'))

@app.route('/')
@login_required
def dashboard():
    rooms = Room.query.filter_by(user_id=current_user.id).all() if current_user.role != 'admin' else Room.query.all()
    # === THÊM ĐOẠN NÀY ĐỂ HIỂN THỊ ĐÚNG TÊN KHÁCH TRONG DANH SÁCH ===
    for room in rooms:
        active_tenant = Tenant.query.join(Contract).filter(
            Tenant.room_id == room.id,
            Contract.end_date >= datetime.now().date()
        ).first()
        if not active_tenant:
            active_tenant = Tenant.query.filter_by(room_id=room.id).order_by(Tenant.id.desc()).first()
        room.tenant = active_tenant
    # ============================================================
    # Tính tổng quan
    total_rooms = len(rooms)
    occupied_rooms = 0
    total_due = 0
    total_paid = 0
    overdue_bills = 0
    current_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    for room in rooms:
        tenant = Tenant.query.filter_by(room_id=room.id).first()
        if tenant:
            occupied_rooms += 1
            contracts = Contract.query.filter_by(tenant_id=tenant.id).all()
            for contract in contracts:
                bills = Bill.query.filter(
                    Bill.contract_id == contract.id,
                    Bill.month >= current_month
                ).all()
                for bill in bills:
                    total_due += bill.total
                    if bill.paid:
                        total_paid += bill.total
                    # Quá hạn nếu qua ngày 5 tháng sau
                    due_date = bill.month.replace(day=28) + timedelta(days=8)  # khoảng ngày 5 tháng sau
                    if datetime.now().date() > due_date:
                        overdue_bills += 1
        # Nếu phòng trống thì không tính gì thêm
    
    total_unpaid = total_due - total_paid
    
    return render_template(
        'dashboard.html', 
        rooms=rooms,
        total_rooms=total_rooms,
        occupied_rooms=occupied_rooms,
        total_due=total_due,
        total_paid=total_paid,
        total_unpaid=total_due - total_paid,
        overdue_bills=overdue_bills
    )

@app.route('/create_room', methods=['GET', 'POST'])
@login_required
def create_room():
    if request.method == 'POST':
        name = request.form['name']
        rent_price = float(request.form['rent_price'])
        internet_fee = float(request.form['internet_fee'])
        new_room = Room(name=name, rent_price=rent_price, internet_fee=internet_fee, user_id=current_user.id)
        db.session.add(new_room)
        db.session.commit()
        flash('Room created')
        return redirect(url_for('dashboard'))
    return render_template('create_room.html')

@app.route('/room/<int:room_id>')
@login_required
def room_detail(room_id):
    room = Room.query.get_or_404(room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Access denied')
        return redirect(url_for('dashboard'))
    tenants = Tenant.query.filter_by(room_id=room_id).all()
    return render_template('room_detail.html', room=room, tenants=tenants)

@app.route('/create_tenant/<int:room_id>', methods=['GET', 'POST'])
@login_required
def create_tenant(room_id):
    room = Room.query.get_or_404(room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Access denied')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        email = request.form['email']
        new_tenant = Tenant(name=name, phone=phone, email=email, room_id=room_id)
        db.session.add(new_tenant)
        db.session.commit()
        flash('Tenant created')
        return redirect(url_for('room_detail', room_id=room_id))
    return render_template('create_tenant.html', room=room)

@app.route('/create_contract/<int:tenant_id>', methods=['GET', 'POST'])
@login_required
def create_contract(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    room = Room.query.get(tenant.room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Access denied')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
        duration_months = int(request.form['duration_months'])
        end_date = start_date + timedelta(days=30 * duration_months)  # Approximate
        new_contract = Contract(tenant_id=tenant_id, start_date=start_date, duration_months=duration_months, end_date=end_date)
        db.session.add(new_contract)
        db.session.commit()
        flash('Contract created')
        return redirect(url_for('tenant_detail', tenant_id=tenant_id))
    return render_template('create_contract.html', tenant=tenant)

@app.route('/tenant/<int:tenant_id>')
@login_required
def tenant_detail(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    room = Room.query.get(tenant.room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Access denied')
        return redirect(url_for('dashboard'))
    contracts = Contract.query.filter_by(tenant_id=tenant_id).all()
    return render_template('tenant_detail.html', tenant=tenant, contracts=contracts)

@app.route('/extend_contract/<int:contract_id>', methods=['POST'])
@login_required
def extend_contract(contract_id):
    contract = Contract.query.get_or_404(contract_id)
    tenant = Tenant.query.get(contract.tenant_id)
    room = Room.query.get(tenant.room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Access denied')
        return redirect(url_for('dashboard'))
    additional_months = int(request.form['additional_months'])
    contract.duration_months += additional_months
    contract.end_date += timedelta(days=30 * additional_months)
    contract.is_extended = True
    db.session.commit()
    flash('Contract extended')
    return redirect(url_for('contract_detail', contract_id=contract_id))

@app.route('/contract/<int:contract_id>')
@login_required
def contract_detail(contract_id):
    contract = Contract.query.get_or_404(contract_id)
    tenant = Tenant.query.get(contract.tenant_id)
    if tenant is None:
        flash('Khách thuê liên kết với hợp đồng không tồn tại. Vui lòng kiểm tra dữ liệu.', 'danger')
        return redirect(url_for('dashboard'))
    room = Room.query.get(tenant.room_id)
    if room is None:
        flash('Phòng liên kết không tồn tại. Vui lòng kiểm tra dữ liệu.', 'danger')
        return redirect(url_for('dashboard'))
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Bạn không có quyền truy cập hợp đồng này', 'danger')
        return redirect(url_for('dashboard'))
    bills = Bill.query.filter_by(contract_id=contract_id).all()
    return render_template('contract_detail.html', contract=contract, tenant=tenant, bills=bills)

@app.route('/create_bill/<int:contract_id>', methods=['GET', 'POST'])
@login_required
def create_bill(contract_id):
    contract = Contract.query.get_or_404(contract_id)
    tenant = Tenant.query.get(contract.tenant_id)
    room = Room.query.get(tenant.room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Access denied')
        return redirect(url_for('dashboard'))

#   # Tìm hóa đơn mới nhất của hợp đồng này để gợi ý chỉ số cũ
    last_bill = Bill.query.filter_by(contract_id=contract_id).order_by(Bill.month.desc()).first()
    
    if request.method == 'POST':
        month_str = request.form['month'] + '-01'
        month = datetime.strptime(month_str, '%Y-%m-%d').date()
        
        # Lấy chỉ số từ form
        electricity_old = float(request.form['electricity_old'])
        electricity_new = float(request.form['electricity_new'])
        water_old = float(request.form['water_old'])
        water_new = float(request.form['water_new'])
        electricity_price = float(request.form['electricity_price'])  # Linh hoạt
        
        # Tính tự động
        total, electricity_usage, water_usage, electricity_cost, water_cost = calculate_bill(
            contract, electricity_old, electricity_new, water_old, water_new, electricity_price
        )
        
        new_bill = Bill(
            contract_id=contract_id, month=month,
            electricity_old=electricity_old, electricity_new=electricity_new,
            water_old=water_old, water_new=water_new,
            electricity_price=electricity_price,
            electricity_usage=electricity_usage, water_usage=water_usage,
            total=total, paid=False
        )
        db.session.add(new_bill)
        db.session.commit()
        flash('Hóa đơn đã được tạo thành công!')
        return redirect(url_for('contract_detail', contract_id=contract_id))
    
    return render_template('create_bill.html', contract=contract, tenant=tenant, room=room, last_bill=last_bill)

@app.route('/pay_bill/<int:bill_id>', methods=['POST'])
@login_required
def pay_bill(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    contract = Contract.query.get(bill.contract_id)
    tenant = Tenant.query.get(contract.tenant_id)
    room = Room.query.get(tenant.room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Access denied')
        return redirect(url_for('dashboard'))
    bill.paid = True
    db.session.commit()
    flash('Bill paid')
    return redirect(url_for('contract_detail', contract_id=bill.contract_id))

@app.route('/bill_print/<int:bill_id>')
@login_required
def bill_print(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    contract = Contract.query.get(bill.contract_id)
    tenant = Tenant.query.get(contract.tenant_id)
    if tenant is None:
        flash('Khách thuê không tồn tại', 'danger')
        return redirect(url_for('dashboard'))
    room = Room.query.get(tenant.room_id)
    if room is None:
        flash('Phòng không tồn tại', 'danger')
        return redirect(url_for('dashboard'))

    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Bạn không có quyền xem hóa đơn này', 'danger')
        return redirect(url_for('dashboard'))

    electricity_cost = bill.electricity_usage * 4000
    water_cost = calculate_water_cost(bill.water_usage)

    if bill.water_usage > 5:
        water_detail = f"5 khối đầu: 5 × 16.000 = 80.000 đ<br>Các khối tiếp: {(bill.water_usage - 5):.2f} × 27.000 = {((bill.water_usage - 5)*27000):,.0f} đ"
    else:
        water_detail = f"5 khối đầu: {bill.water_usage:.2f} × 16.000 = {water_cost:,.0f} đ<br>Các khối tiếp: 0 × 27.000 = 0 đ"

    return render_template('bill_print.html',
                           bill=bill,
                           tenant=tenant,
                           room=room,
                           electricity_cost=electricity_cost,
                           water_cost=water_cost,
                           water_detail=water_detail)

# --- Sửa hợp đồng ---
@app.route('/edit_contract/<int:contract_id>', methods=['GET', 'POST'])
@login_required
def edit_contract(contract_id):
    contract = Contract.query.get_or_404(contract_id)
    tenant = Tenant.query.get(contract.tenant_id)
    room = Room.query.get(tenant.room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Bạn không có quyền chỉnh sửa hợp đồng này', 'danger')
        return redirect(url_for('dashboard'))

    if Bill.query.filter_by(contract_id=contract_id).first():
        flash('Không thể sửa hợp đồng đã có hóa đơn', 'danger')
        return redirect(url_for('contract_detail', contract_id=contract_id))

    if request.method == 'POST':
        contract.start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
        contract.duration_months = int(request.form['duration_months'])
        contract.end_date = contract.start_date + timedelta(days=30 * contract.duration_months)  # xấp xỉ
        db.session.commit()
        flash('Hợp đồng đã được cập nhật thành công!', 'success')
        return redirect(url_for('contract_detail', contract_id=contract_id))

    return render_template('edit_contract.html', contract=contract, tenant=tenant)

# --- Xóa hợp đồng ---
@app.route('/delete_contract/<int:contract_id>', methods=['POST'])
@login_required
def delete_contract(contract_id):
    contract = Contract.query.get_or_404(contract_id)
    tenant = Tenant.query.get(contract.tenant_id)
    room = Room.query.get(tenant.room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Bạn không có quyền', 'danger')
        return redirect(url_for('dashboard'))

    if Bill.query.filter_by(contract_id=contract_id).first():
        flash('Không thể xóa hợp đồng đã có hóa đơn', 'danger')
        return redirect(url_for('contract_detail', contract_id=contract_id))

    db.session.delete(contract)
    db.session.commit()
    flash('Hợp đồng đã được xóa thành công', 'success')
    return redirect(url_for('tenant_detail', tenant_id=tenant.id))

# --- Sửa hóa đơn ---
@app.route('/edit_bill/<int:bill_id>', methods=['GET', 'POST'])
@login_required
def edit_bill(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    contract = Contract.query.get(bill.contract_id)
    tenant = Tenant.query.get(contract.tenant_id)
    room = Room.query.get(tenant.room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Bạn không có quyền', 'danger')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        bill.month = datetime.strptime(request.form['month'] + '-01', '%Y-%m-%d').date()
        
        # Lấy chỉ số mới từ form
        bill.electricity_old = float(request.form['electricity_old'])
        bill.electricity_new = float(request.form['electricity_new'])
        bill.water_old = float(request.form['water_old'])
        bill.water_new = float(request.form['water_new'])
        bill.electricity_price = float(request.form['electricity_price'])
        
        # Tính lại tự động
        bill.total, bill.electricity_usage, bill.water_usage, _, _ = calculate_bill(
            contract, bill.electricity_old, bill.electricity_new, bill.water_old, bill.water_new, bill.electricity_price
        )
        
        db.session.commit()
        flash('Hóa đơn đã được cập nhật!')
        return redirect(url_for('contract_detail', contract_id=contract.id))
    
    return render_template('edit_bill.html', bill=bill, tenant=tenant, room=room, contract=contract)

# --- Xóa hóa đơn ---
@app.route('/delete_bill/<int:bill_id>', methods=['POST'])
@login_required
def delete_bill(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    contract = Contract.query.get(bill.contract_id)
    tenant = Tenant.query.get(contract.tenant_id)
    room = Room.query.get(tenant.room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Bạn không có quyền', 'danger')
        return redirect(url_for('dashboard'))

    db.session.delete(bill)
    db.session.commit()
    flash('Hóa đơn đã được xóa', 'success')
    return redirect(url_for('contract_detail', contract_id=contract.id))

@app.route('/tenant_login', methods=['GET', 'POST'])
def tenant_login():
    if current_user.is_authenticated:
        return redirect(url_for('tenant_dashboard' if current_user.role == 'tenant' else 'dashboard'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form.get('password', '')  # Mật khẩu có thể để trống nếu không đặt
        
        user = User.query.filter_by(username=username, role='tenant').first()
        if user and (not user.password or check_password_hash(user.password, password)):
            login_user(user)
            return redirect(url_for('tenant_dashboard'))
        flash('Tên đăng nhập hoặc mật khẩu sai', 'danger')
    
    return render_template('tenant_login.html')

@app.route('/tenant_dashboard')
@login_required
def tenant_dashboard():
    if current_user.role != 'tenant' or not current_user.tenant_id:
        flash('Truy cập không hợp lệ', 'danger')
        logout_user()
        return redirect(url_for('tenant_login'))
    
    tenant = Tenant.query.get(current_user.tenant_id)
    if not tenant:
        flash('Không tìm thấy thông tin phòng', 'danger')
        logout_user()
        return redirect(url_for('tenant_login'))
    
    room = Room.query.get(tenant.room_id)
    contracts = Contract.query.filter_by(tenant_id=tenant.id).all()
    bills = []
    for contract in contracts:
        bills.extend(Bill.query.filter_by(contract_id=contract.id).order_by(Bill.month.desc()).all())
    
    return render_template('tenant_dashboard.html', tenant=tenant, room=room, bills=bills)

@app.route('/tenant_logout')
@login_required
def tenant_logout():
    logout_user()
    flash('Bạn đã đăng xuất', 'info')
    return redirect(url_for('tenant_login'))
    
# Initialize DB and create default admin
if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # Tạo bảng nếu chưa có (an toàn nếu đã có)

        # Chỉ tạo admin nếu chưa tồn tại
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                password=generate_password_hash('admin', method='pbkdf2:sha256'),
                role='admin'
            )
            db.session.add(admin)
            db.session.commit()
            print("Tài khoản admin đã được tạo thành công!")
        else:
            print("Tài khoản admin đã tồn tại.")
    # Render tự set biến PORT, mặc định fallback 5000 nếu local
    port = int(os.environ.get('PORT', 5000))
    # Bind 0.0.0.0 để Render truy cập được
    app.run(host='0.0.0.0', port=port, debug=False)  # debug=False cho production

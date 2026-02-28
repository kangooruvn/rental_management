from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key_here_change_in_production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///rental.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
    'pool_size': 5,
    'max_overflow': 10,
    'pool_timeout': 30
}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# VAT và bậc thang giá điện EVN (chưa VAT)
VAT_RATE = 0.08
EVN_TIERS = [
    (0, 50, 1984),
    (50, 100, 2050),
    (100, 200, 2380),
    (200, 300, 2998),
    (300, 400, 3350),
    (400, None, 3460),
]

# Models
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False)

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    rent_price = db.Column(db.Float, nullable=False)
    internet_fee = db.Column(db.Float, nullable=False)
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
    end_date = db.Column(db.Date, nullable=False)
    is_extended = db.Column(db.Boolean, default=False)

class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey('contract.id'), nullable=False)
    month = db.Column(db.Date, nullable=False)
    electricity_old = db.Column(db.Float, default=0.0)
    electricity_new = db.Column(db.Float, default=0.0)
    water_old = db.Column(db.Float, default=0.0)
    water_new = db.Column(db.Float, default=0.0)
    electricity_usage = db.Column(db.Float, default=0.0)
    water_usage = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, nullable=False)
    paid = db.Column(db.Boolean, default=False)

class TotalElectricityMonth(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.Date, nullable=False)
    electricity_old = db.Column(db.Float, default=0.0)
    electricity_new = db.Column(db.Float, default=0.0)
    total_kwh = db.Column(db.Float, default=0.0)
    average_price = db.Column(db.Float, default=0.0)

# Hàm tính tiền nước
def calculate_water_cost(water_usage):
    if water_usage <= 0:
        return 0.0
    if water_usage <= 5:
        return water_usage * 16000
    else:
        return 5 * 16000 + (water_usage - 5) * 27000

# Lấy tổng kWh từ TotalElectricityMonth
def get_total_electricity_usage_in_month(month_date):
    entry = TotalElectricityMonth.query.filter_by(month=month_date).first()
    if entry:
        return entry.total_kwh
    return 0.0

# Tính tổng tiền điện chung theo bậc thang
def calculate_total_electricity_cost_before_vat(total_kwh):
    if total_kwh <= 0:
        return 0.0
    cost = 0.0
    remaining = total_kwh
    for low, high, price in EVN_TIERS:
        tier_size = (high or float('inf')) - low
        used = min(remaining, tier_size)
        cost += used * price
        remaining -= used
        if remaining <= 0:
            break
    return cost

# Hàm tính hóa đơn
def calculate_bill(contract, electricity_old, electricity_new, water_old, water_new, bill_month):
    room = Room.query.get(Tenant.query.get(contract.tenant_id).room_id)
    
    electricity_usage = max(electricity_new - electricity_old, 0)
    water_usage = max(water_new - water_old, 0)
    
    water_cost = calculate_water_cost(water_usage)
    
    total_month_kwh = get_total_electricity_usage_in_month(bill_month) + electricity_usage
    
    total_month_cost_before_vat = calculate_total_electricity_cost_before_vat(total_month_kwh)
    
    average_price = total_month_cost_before_vat / total_month_kwh if total_month_kwh > 0 else 0.0
    
    room_electricity_before_vat = electricity_usage * average_price
    
    room_electricity_with_vat = room_electricity_before_vat * (1 + VAT_RATE)
    
    total = room.rent_price + room.internet_fee + room_electricity_with_vat + water_cost
    
    return {
        'total': total,
        'electricity_usage': electricity_usage,
        'water_usage': water_usage,
        'room_electricity_before_vat': room_electricity_before_vat,
        'electricity_vat': room_electricity_before_vat * VAT_RATE,
        'room_electricity_with_vat': room_electricity_with_vat,
        'water_cost': water_cost,
        'average_price_before_vat': average_price
    }

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        confirm_password = request.form.get('confirm_password', '')
        
        if password != confirm_password:
            flash('Mật khẩu xác nhận không khớp!', 'danger')
            return render_template('signup.html')
        
        if User.query.filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại. Vui lòng chọn tên khác.', 'danger')
            return render_template('signup.html')
        
        new_user = User(
            username=username,
            password=generate_password_hash(password, method='pbkdf2:sha256'),
            role='user'
        )
        db.session.add(new_user)
        db.session.commit()
        
        login_user(new_user)
        flash(
            f'Chào mừng {new_user.username} đến với Hệ thống Quản lý Phòng Trọ! 🎉\n'
            'Bạn đã đăng ký thành công. Bây giờ bạn có thể bắt đầu thêm phòng và quản lý riêng của mình.\n'
            'Chúc bạn sử dụng vui vẻ!',
            'success'
        )
        return redirect(url_for('dashboard'))
    
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Đăng nhập thất bại', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Đăng xuất thành công', 'info')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    rooms = Room.query.filter_by(user_id=current_user.id).all() if current_user.role != 'admin' else Room.query.all()
    return render_template('dashboard.html', rooms=rooms)

@app.route('/create_bill/<int:contract_id>', methods=['GET', 'POST'])
@login_required
def create_bill(contract_id):
    contract = Contract.query.get_or_404(contract_id)
    tenant = Tenant.query.get(contract.tenant_id)
    room = Room.query.get(tenant.room_id)
    if current_user.role != 'admin' and room.user_id != current_user.id:
        flash('Bạn không có quyền', 'danger')
        return redirect(url_for('dashboard'))
    
    last_bill = Bill.query.filter_by(contract_id=contract_id).order_by(Bill.month.desc()).first()
    
    # Lấy tháng để tính đơn giá trung bình (ưu tiên từ query param nếu có)
    preview_month_str = request.args.get('month')
    if preview_month_str:
        preview_month = datetime.strptime(preview_month_str + '-01', '%Y-%m-%d').date()
    else:
        preview_month = datetime.now().replace(day=1).date()
    
    total_kwh_preview = get_total_electricity_usage_in_month(preview_month)
    total_cost_preview = calculate_total_electricity_cost_before_vat(total_kwh_preview)
    average_price_preview = total_cost_preview / total_kwh_preview if total_kwh_preview > 0 else 0
    
    if request.method == 'POST':
        try:
            month_str = request.form['month'] + '-01'
            month = datetime.strptime(month_str, '%Y-%m-%d').date()
            
            electricity_old = float(request.form.get('electricity_old', 0))
            electricity_new = float(request.form.get('electricity_new', 0))
            water_old = float(request.form.get('water_old', 0))
            water_new = float(request.form.get('water_new', 0))
            
            bill_data = calculate_bill(contract, electricity_old, electricity_new, water_old, water_new, month)
            
            new_bill = Bill(
                contract_id=contract_id,
                month=month,
                electricity_old=electricity_old,
                electricity_new=electricity_new,
                water_old=water_old,
                water_new=water_new,
                electricity_usage=bill_data['electricity_usage'],
                water_usage=bill_data['water_usage'],
                total=bill_data['total'],
                paid=False
            )
            db.session.add(new_bill)
            db.session.commit()
            flash('Hóa đơn đã được tạo thành công!', 'success')
            return redirect(url_for('contract_detail', contract_id=contract_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi khi tạo hóa đơn: {str(e)}. Vui lòng kiểm tra lại chỉ số hoặc thử lại.', 'danger')
            return redirect(url_for('create_bill', contract_id=contract_id))
    
    return render_template(
        'create_bill.html',
        contract=contract,
        tenant=tenant,
        room=room,
        last_bill=last_bill,
        average_price_preview=round(average_price_preview, 0),
        preview_month=preview_month.strftime('%Y-%m')
    )

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                password=generate_password_hash('admin', method='pbkdf2:sha256'),
                role='admin'
            )
            db.session.add(admin)
            db.session.commit()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

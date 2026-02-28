from flask import Flask, render_template, request, redirect, url_for, flash, make_response, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///rental.db').replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# VAT và bậc thang
VAT_RATE = 0.08
EVN_TIERS = [
    (0, 50, 1984),
    (50, 100, 2050),
    (100, 200, 2380),
    (200, 300, 2998),
    (300, 400, 3350),
    (400, None, 3460),
]

# Models (thêm TotalElectricityMonth nếu chưa có)
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
    # Fallback nếu chưa có
    bills = Bill.query.filter_by(month=month_date).all()
    return sum(bill.electricity_usage for bill in bills) if bills else 0.0

# Tính tổng tiền điện toàn nhà trước VAT
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

# Hàm tính hóa đơn (fix lỗi operand: chỉ nhân float * float, không nhân date)
def calculate_bill(contract, electricity_old, electricity_new, water_old, water_new, bill_month):
    room = Room.query.get(Tenant.query.get(contract.tenant_id).room_id)
    
    electricity_usage = max(electricity_new - electricity_old, 0)
    water_usage = max(water_new - water_old, 0)
    
    water_cost = calculate_water_cost(water_usage)
    
    total_month_kwh = get_total_electricity_usage_in_month(bill_month)
    
    total_month_cost_before_vat = calculate_total_electricity_cost_before_vat(total_month_kwh)
    
    average_price = (total_month_cost_before_vat / total_month_kwh) if total_month_kwh > 0 else 0.0  # Fix chia 0
    
    room_electricity_before_vat = electricity_usage * average_price  # Chỉ nhân float * float
    
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

# Route create_bill (thêm preview đơn giá)
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
    
    # Preview đơn giá cho tháng hiện tại
    preview_month = datetime.now().replace(day=1).date()
    total_kwh_preview = get_total_electricity_usage_in_month(preview_month)
    total_cost_preview = calculate_total_electricity_cost_before_vat(total_kwh_preview)
    average_price_preview = (total_cost_preview / total_kwh_preview) if total_kwh_preview > 0 else 0.0
    
    if request.method == 'POST':
        month_str = request.form['month'] + '-01'
        month = datetime.strptime(month_str, '%Y-%m-%d').date()
        
        electricity_old = float(request.form['electricity_old'])
        electricity_new = float(request.form['electricity_new'])
        water_old = float(request.form['water_old'])
        water_new = float(request.form['water_new'])
        
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
    
    return render_template('create_bill.html', contract=contract, tenant=tenant, room=room, last_bill=last_bill, average_price_preview=average_price_preview)

# Các route khác giữ nguyên...

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Tạo admin nếu chưa có...
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)  # Debug để thấy lỗi

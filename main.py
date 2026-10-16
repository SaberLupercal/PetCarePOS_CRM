import os
from decimal import Decimal
from datetime import datetime, date
import psycopg2
from psycopg2.extras import RealDictCursor, execute_batch
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://postgres:Candy_thoi05@crm-petcarespa.ctueigiw2vwf.ap-southeast-2.rds.amazonaws.com:5432/crm_petcarespa"

app = FastAPI(title="Pet Care Spa POS & CRM API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def format_row(row):
    if not row:
        return row
    formatted = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            formatted[k] = float(v)
        elif isinstance(v, (datetime, date)):
            formatted[k] = v.isoformat()
        else:
            formatted[k] = v
    return formatted

# ==========================================
# 1. AUTHENTICATION & LOGIN
# ==========================================
class LoginSchema(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
def login(payload: LoginSchema):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 
                s.staff_id, 
                s.staff_code, 
                s.full_name, 
                s.role, 
                s.branch_id,
                b.branch_name,
                s.password_hash
            FROM staffs s
            LEFT JOIN branches b ON s.branch_id = b.branch_id
            WHERE s.username = %s AND s.is_active = TRUE;
        """, (payload.username.strip(),))
        user = cur.fetchone()

        if not user or user["password_hash"] != payload.password.strip():
            raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu không chính xác!")

        del user["password_hash"]
        return {"status": "success", "user": format_row(user)}
    finally:
        cur.close()
        conn.close()

# ==========================================
# 2. DASHBOARD OVERVIEW (HÔM NAY / HÔM QUA & THÁNG NÀY)
# ==========================================
@app.get("/api/dashboard/overview")
def get_dashboard_overview(branch_id: int = 1, timeframe: str = "today"):
    conn = get_connection()
    cur = conn.cursor()
    try:
        if timeframe == "yesterday":
            date_filter_orders = "(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date = CURRENT_DATE - INTERVAL '1 day'"
            date_filter_apts = "appointment_date::date = CURRENT_DATE - INTERVAL '1 day'"
            date_filter_cust = "(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date = CURRENT_DATE - INTERVAL '1 day'"
            cat_date_filter = "(o.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date = CURRENT_DATE - INTERVAL '1 day'"
        else:
            date_filter_orders = "(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date = CURRENT_DATE"
            date_filter_apts = "appointment_date::date = CURRENT_DATE"
            date_filter_cust = "(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date = CURRENT_DATE"
            cat_date_filter = "(o.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')::date = CURRENT_DATE"

        cur.execute(f"""
            SELECT 
                COALESCE(SUM(final_amount), 0) AS total_rev,
                COALESCE(SUM(CASE WHEN payment_method = 'Cash' THEN final_amount ELSE 0 END), 0) AS rev_cash,
                COALESCE(SUM(CASE WHEN payment_method IN ('Transfer', 'Banking', 'VietQR', 'QR') THEN final_amount ELSE 0 END), 0) AS rev_transfer
            FROM orders
            WHERE branch_id = %s 
              AND payment_status = 'Completed'
              AND {date_filter_orders};
        """, (branch_id,))
        rev_info = cur.fetchone()

        cur.execute(f"""
            SELECT COUNT(*) AS completed_apts
            FROM appointments
            WHERE branch_id = %s 
              AND status = 'Completed'
              AND {date_filter_apts};
        """, (branch_id,))
        completed_apts = cur.fetchone()["completed_apts"]

        cur.execute(f"""
            SELECT COUNT(*) AS new_cust
            FROM customers
            WHERE home_branch_id = %s
              AND {date_filter_cust};
        """, (branch_id,))
        new_cust = cur.fetchone()["new_cust"]

        cur.execute(f"""
            SELECT COUNT(*) AS waiting_apts
            FROM appointments
            WHERE branch_id = %s 
              AND status IN ('Scheduled', 'Confirmed')
              AND {date_filter_apts};
        """, (branch_id,))
        waiting_apts = cur.fetchone()["waiting_apts"]

        start_month_expr_orders = "(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok') >= date_trunc('month', (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Bangkok'))"
        start_month_expr_cust   = "(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok') >= date_trunc('month', (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Bangkok'))"
        start_month_expr_pets   = "(p.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok') >= date_trunc('month', (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Bangkok'))"

        cur.execute(f"""
            SELECT COUNT(*) AS total_cust 
            FROM customers 
            WHERE home_branch_id = %s
              AND {start_month_expr_cust};
        """, (branch_id,))
        total_cust = cur.fetchone()["total_cust"]

        cur.execute(f"""
            SELECT COUNT(*) AS total_pets 
            FROM pets p
            JOIN customers c ON p.customer_id = c.customer_id
            WHERE c.home_branch_id = %s
              AND {start_month_expr_pets};
        """, (branch_id,))
        total_pets = cur.fetchone()["total_pets"]

        cur.execute(f"""
            SELECT COALESCE(SUM(final_amount), 0) AS total_rev_month 
            FROM orders 
            WHERE branch_id = %s 
              AND payment_status = 'Completed'
              AND {start_month_expr_orders};
        """, (branch_id,))
        total_month_rev = cur.fetchone()["total_rev_month"]

        cur.execute(f"""
            SELECT 
                COUNT(*) AS total_buyers,
                COUNT(CASE WHEN order_count >= 2 THEN 1 END) AS repeat_buyers
            FROM (
                SELECT customer_id, COUNT(*) AS order_count
                FROM orders
                WHERE branch_id = %s 
                  AND customer_id IS NOT NULL 
                  AND payment_status = 'Completed'
                  AND {start_month_expr_orders}
                GROUP BY customer_id
            ) sub;
        """, (branch_id,))
        rep = cur.fetchone()
        repeat_rate = 0.0
        if rep and rep["total_buyers"] > 0:
            repeat_rate = round((rep["repeat_buyers"] / rep["total_buyers"]) * 100, 1)

        cur.execute(f"""
            SELECT 
                COALESCE(sp.category, 'Khác') AS cat_name,
                COALESCE(SUM(oi.quantity), 0) AS total_qty
            FROM orders o
            JOIN order_items oi ON o.order_id = oi.order_id
            JOIN services_products sp ON oi.item_id = sp.item_id
            WHERE o.branch_id = %s 
              AND o.payment_status = 'Completed'
              AND {cat_date_filter}
            GROUP BY sp.category;
        """, (branch_id,))
        cat_rows = cur.fetchall()
        category_stats = {r["cat_name"]: int(r["total_qty"]) for r in cat_rows}

        return {
            "period": {
                "revenue": float(rev_info["total_rev"]),
                "cash": float(rev_info["rev_cash"]),
                "transfer": float(rev_info["rev_transfer"]),
                "completed_apts": completed_apts,
                "new_cust": new_cust,
                "waiting_apts": waiting_apts
            },
            "accumulated": {
                "total_customers": total_cust,
                "total_pets": total_pets,
                "total_revenue": float(total_month_rev),
                "repeat_rate": repeat_rate
            },
            "category_stats": category_stats
        }
    finally:
        cur.close()
        conn.close()

# ==========================================
# 3. QUẢN LÝ NHÂN SỰ
# ==========================================
class StaffCreateSchema(BaseModel):
    branch_id: int
    full_name: str
    role: str
    username: str
    password: str

class StaffUpdateSchema(BaseModel):
    full_name: str
    role: str
    password: Optional[str] = None
    is_active: bool = True

@app.get("/api/staffs")
def get_branch_staffs(branch_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT staff_id, staff_code, full_name, role, branch_id, username, is_active, created_at
            FROM staffs
            WHERE branch_id = %s
            ORDER BY staff_id ASC;
        """, (branch_id,))
        return [format_row(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

@app.post("/api/staffs")
def create_staff(payload: StaffCreateSchema):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT staff_id FROM staffs WHERE username = %s;", (payload.username.strip(),))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Tên đăng nhập này đã tồn tại!")

        cur.execute("""
            INSERT INTO staffs (branch_id, full_name, role, username, password_hash, is_active)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            RETURNING staff_id, staff_code, full_name, role, branch_id, username, is_active;
        """, (payload.branch_id, payload.full_name.strip(), payload.role, payload.username.strip(), payload.password.strip()))
        new_staff = format_row(cur.fetchone())
        conn.commit()
        return {"status": "success", "staff": new_staff}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.put("/api/staffs/{staff_id}")
def update_staff(staff_id: int, payload: StaffUpdateSchema):
    conn = get_connection()
    cur = conn.cursor()
    try:
        if payload.password and payload.password.strip():
            cur.execute("""
                UPDATE staffs
                SET full_name = %s, role = %s, password_hash = %s, is_active = %s
                WHERE staff_id = %s
                RETURNING staff_id, staff_code, full_name, role, branch_id, username, is_active;
            """, (payload.full_name.strip(), payload.role, payload.password.strip(), payload.is_active, staff_id))
        else:
            cur.execute("""
                UPDATE staffs
                SET full_name = %s, role = %s, is_active = %s
                WHERE staff_id = %s
                RETURNING staff_id, staff_code, full_name, role, branch_id, username, is_active;
            """, (payload.full_name.strip(), payload.role, payload.is_active, staff_id))

        updated = format_row(cur.fetchone())
        if not updated:
            raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên")
        conn.commit()
        return {"status": "success", "staff": updated}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.delete("/api/staffs/{staff_id}")
def delete_staff(staff_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE staffs SET is_active = FALSE WHERE staff_id = %s RETURNING staff_id;", (staff_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên")
        conn.commit()
        return {"status": "success", "message": "Đã vô hiệu hóa nhân viên thành công"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

# ==========================================
# 4. KHÁCH HÀNG (CÓ PHÂN TRANG) & POS INIT-DATA
# ==========================================
@app.get("/api/pos/init-data")
def get_pos_init_data(branch_id: Optional[int] = None):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cust_query = """
            SELECT 
                c.customer_id, 
                c.customer_code, 
                c.full_name, 
                c.phone_number, 
                c.email,
                c.gender,
                c.date_of_birth,
                c.address,
                c.home_branch_id,
                b.branch_name AS home_branch_name,
                COALESCE(SUM(o.final_amount), 0) AS total_spending,
                CASE 
                    WHEN COALESCE(SUM(o.final_amount), 0) >= 60000000 THEN 'Platinum'
                    WHEN COALESCE(SUM(o.final_amount), 0) >= 30000000 THEN 'Gold'
                    WHEN COALESCE(SUM(o.final_amount), 0) >= 6000000  THEN 'Silver'
                    ELSE 'Standard'
                END AS tier
            FROM customers c
            LEFT JOIN branches b ON c.home_branch_id = b.branch_id
            LEFT JOIN orders o ON c.customer_id = o.customer_id AND o.payment_status = 'Completed'
        """
        params = []
        if branch_id:
            cust_query += " WHERE c.home_branch_id = %s"
            params.append(branch_id)

        cust_query += " GROUP BY c.customer_id, b.branch_name ORDER BY c.customer_id DESC;"
        cur.execute(cust_query, tuple(params))
        customers = [format_row(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT 
                pet_id, pet_code, customer_id, name, species, breed, 
                weight_kg, birth_date, medical_history 
            FROM pets 
            ORDER BY pet_id DESC;
        """)
        pets = [format_row(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT item_id, item_code, item_name, item_type, category, base_price, unit, stock_quantity 
            FROM services_products 
            WHERE is_active = TRUE 
            ORDER BY category, item_name;
        """)
        items = [format_row(r) for r in cur.fetchall()]

        staff_query = "SELECT staff_id, staff_code, full_name, role, branch_id FROM staffs WHERE is_active = TRUE"
        s_params = []
        if branch_id:
            staff_query += " AND branch_id = %s"
            s_params.append(branch_id)
        staff_query += " ORDER BY staff_id ASC;"
        cur.execute(staff_query, tuple(s_params))
        staffs = [format_row(r) for r in cur.fetchall()]

        return {
            "customers": customers,
            "pets": pets,
            "catalog": items,
            "staffs": staffs
        }
    finally:
        cur.close()
        conn.close()

@app.get("/api/customers")
def get_customers(branch_id: Optional[int] = None, limit: int = 50, offset: int = 0, search: Optional[str] = None):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cust_query = """
            SELECT 
                c.customer_id, 
                c.customer_code, 
                c.full_name, 
                c.phone_number, 
                c.email,
                c.gender,
                c.date_of_birth,
                c.address,
                c.home_branch_id,
                b.branch_name AS home_branch_name,
                COALESCE(SUM(o.final_amount), 0) AS total_spending,
                CASE 
                    WHEN COALESCE(SUM(o.final_amount), 0) >= 60000000 THEN 'Platinum'
                    WHEN COALESCE(SUM(o.final_amount), 0) >= 30000000 THEN 'Gold'
                    WHEN COALESCE(SUM(o.final_amount), 0) >= 6000000  THEN 'Silver'
                    ELSE 'Standard'
                END AS tier
            FROM customers c
            LEFT JOIN branches b ON c.home_branch_id = b.branch_id
            LEFT JOIN orders o ON c.customer_id = o.customer_id AND o.payment_status = 'Completed'
        """
        params = []
        conditions = []

        if branch_id:
            conditions.append("c.home_branch_id = %s")
            params.append(branch_id)
        
        if search and search.strip():
            keyword = f"%{search.strip().lower()}%"
            conditions.append("(LOWER(c.full_name) LIKE %s OR c.phone_number LIKE %s OR LOWER(c.customer_code) LIKE %s)")
            params.extend([keyword, keyword, keyword])

        if conditions:
            cust_query += " WHERE " + " AND ".join(conditions)

        cust_query += " GROUP BY c.customer_id, b.branch_name ORDER BY c.customer_id DESC LIMIT %s OFFSET %s;"
        params.extend([limit, offset])

        cur.execute(cust_query, tuple(params))
        customers = [format_row(r) for r in cur.fetchall()]

        count_query = "SELECT COUNT(*) AS total FROM customers c"
        count_params = []
        if conditions:
            count_query += " WHERE " + " AND ".join(conditions)
            count_params = params[:-2]

        cur.execute(count_query, tuple(count_params))
        total_count = cur.fetchone()["total"]

        return {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "customers": customers
        }
    finally:
        cur.close()
        conn.close()

@app.get("/api/customers/search")
def search_customer_by_phone(phone: str):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 
                c.customer_id, c.customer_code, c.full_name, c.phone_number, 
                c.home_branch_id, b.branch_name AS home_branch_name, c.tier
            FROM customers c
            LEFT JOIN branches b ON c.home_branch_id = b.branch_id
            WHERE c.phone_number = %s;
        """, (phone.strip(),))
        cust = cur.fetchone()
        if not cust:
            return {"found": False}
        return {"found": True, "customer": format_row(cust)}
    finally:
        cur.close()
        conn.close()

class InitialPetSchema(BaseModel):
    name: str
    species: str
    breed: Optional[str] = None
    weight_kg: Optional[float] = None
    birth_date: Optional[str] = None
    medical_history: Optional[str] = None

class CreateCustomerWithPetSchema(BaseModel):
    branch_id: int = 1
    full_name: str
    phone_number: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    address: Optional[str] = None
    tier: str = "Standard"
    initial_pet: Optional[InitialPetSchema] = None

class UpdateCustomerSchema(BaseModel):
    full_name: str
    phone_number: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    address: Optional[str] = None

@app.post("/api/customers")
def create_customer(payload: CreateCustomerWithPetSchema):
    conn = get_connection()
    cur = conn.cursor()
    try:
        phone = payload.phone_number.strip() if payload.phone_number else None
        dob = payload.date_of_birth if payload.date_of_birth else None

        if phone:
            cur.execute("SELECT customer_id FROM customers WHERE phone_number = %s;", (phone,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="Số điện thoại này đã tồn tại trong hệ thống PetCare Spa!")

        cur.execute("""
            INSERT INTO customers (home_branch_id, full_name, phone_number, gender, date_of_birth, address, tier)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING customer_id, customer_code, full_name, phone_number, gender, date_of_birth, address, tier, home_branch_id;
        """, (payload.branch_id, payload.full_name.strip(), phone, payload.gender, dob, payload.address, "Standard"))
        new_cust = format_row(cur.fetchone())
        cust_id = new_cust["customer_id"]

        if payload.initial_pet and payload.initial_pet.name.strip():
            pet = payload.initial_pet
            p_dob = pet.birth_date if pet.birth_date else None
            cur.execute("""
                INSERT INTO pets (customer_id, name, species, breed, weight_kg, birth_date, medical_history)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING pet_id, pet_code, name, species, breed, weight_kg, birth_date, medical_history;
            """, (cust_id, pet.name.strip(), pet.species, pet.breed, pet.weight_kg, p_dob, pet.medical_history))

        conn.commit()
        return {"status": "success", "customer": new_cust}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.put("/api/customers/{customer_id}")
def update_customer(customer_id: int, payload: UpdateCustomerSchema):
    conn = get_connection()
    cur = conn.cursor()
    try:
        phone = payload.phone_number.strip() if payload.phone_number else None
        dob = payload.date_of_birth if payload.date_of_birth else None

        if phone:
            cur.execute("""
                SELECT customer_id FROM customers 
                WHERE phone_number = %s AND customer_id != %s;
            """, (phone, customer_id))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="Số điện thoại này đã được sử dụng bởi khách hàng khác!")

        cur.execute("""
            UPDATE customers 
            SET full_name = %s, phone_number = %s, gender = %s, date_of_birth = %s, address = %s
            WHERE customer_id = %s
            RETURNING customer_id, customer_code, full_name, phone_number, gender, date_of_birth, address, tier;
        """, (payload.full_name.strip(), phone, payload.gender, dob, payload.address, customer_id))
        
        updated = format_row(cur.fetchone())
        if not updated:
            raise HTTPException(status_code=404, detail="Không tìm thấy khách hàng để cập nhật")

        conn.commit()
        return {"status": "success", "customer": updated}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.delete("/api/customers/{customer_id}")
def delete_customer(customer_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS total FROM orders WHERE customer_id = %s;", (customer_id,))
        order_count = cur.fetchone()["total"]
        if order_count > 0:
            raise HTTPException(
                status_code=400, 
                detail=f"Không thể xóa khách hàng này vì đã có {order_count} đơn hàng trong hệ thống!"
            )
        
        cur.execute("DELETE FROM pets WHERE customer_id = %s;", (customer_id,))
        cur.execute("DELETE FROM customers WHERE customer_id = %s;", (customer_id,))
        conn.commit()
        return {"status": "success", "message": "Đã xóa khách hàng thành công"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

# ==========================================
# 5. PETS & CARE RECORDS
# ==========================================
class AddPetSchema(BaseModel):
    customer_id: int
    name: str
    species: str
    breed: Optional[str] = None
    weight_kg: Optional[float] = None
    birth_date: Optional[str] = None
    medical_history: Optional[str] = None

@app.post("/api/pets")
def add_pet(payload: AddPetSchema):
    conn = get_connection()
    cur = conn.cursor()
    try:
        p_dob = payload.birth_date if payload.birth_date else None
        cur.execute("""
            INSERT INTO pets (customer_id, name, species, breed, weight_kg, birth_date, medical_history)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING pet_id, pet_code, customer_id, name, species, breed, weight_kg, birth_date, medical_history;
        """, (payload.customer_id, payload.name.strip(), payload.species, payload.breed, payload.weight_kg, p_dob, payload.medical_history))
        new_pet = format_row(cur.fetchone())
        conn.commit()
        return {"status": "success", "pet": new_pet}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

class CreateCareRecordSchema(BaseModel):
    pet_id: int
    service_id: Optional[int] = None
    service_name: str
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    rating: Optional[str] = "Rất tốt"
    notes: Optional[str] = None

@app.get("/api/pets/{pet_id}/care-records")
def get_pet_care_records(pet_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT record_id, pet_id, care_date, service_name, staff_name, rating, notes
            FROM pet_care_records
            WHERE pet_id = %s
            ORDER BY care_date DESC;
        """, (pet_id,))
        return [format_row(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

@app.post("/api/pets/{pet_id}/care-records")
def create_pet_care_record(pet_id: int, payload: CreateCareRecordSchema):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO pet_care_records (pet_id, service_id, service_name, staff_id, staff_name, rating, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING record_id, pet_id, care_date, service_name, staff_name, rating, notes;
        """, (
            pet_id, payload.service_id, payload.service_name, 
            payload.staff_id, payload.staff_name, payload.rating, payload.notes
        ))
        new_record = format_row(cur.fetchone())
        conn.commit()
        return {"status": "success", "record": new_record}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

# ==========================================
# 6. APPOINTMENTS (LỊCH HẸN THEO BRANCH)
# ==========================================
class AppointmentItemDetailSchema(BaseModel):
    pet_id: int
    item_id: int

class CreateAppointmentSchema(BaseModel):
    branch_id: int = 1
    staff_id: Optional[int] = None
    customer_id: int
    appointment_date: str
    status: str = "Scheduled"
    notes: Optional[str] = None
    items: Optional[List[AppointmentItemDetailSchema]] = []

@app.get("/api/appointments")
def get_appointments(branch_id: Optional[int] = None):
    conn = get_connection()
    cur = conn.cursor()
    try:
        query = """
            SELECT 
                a.appointment_id,
                a.appointment_code,
                a.appointment_date,
                a.status,
                a.notes,
                a.branch_id,
                c.full_name AS customer_name,
                c.phone_number
            FROM appointments a
            LEFT JOIN customers c ON a.customer_id = c.customer_id
        """
        params = []
        if branch_id:
            query += " WHERE a.branch_id = %s"
            params.append(branch_id)

        query += " ORDER BY a.appointment_date DESC;"
        cur.execute(query, tuple(params))
        return [format_row(r) for r in cur.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.post("/api/appointments")
def create_appointment(payload: CreateAppointmentSchema):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO appointments (branch_id, staff_id, customer_id, appointment_date, status, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING appointment_id, appointment_code;
        """, (
            payload.branch_id, payload.staff_id, payload.customer_id, 
            payload.appointment_date, payload.status, payload.notes
        ))
        res = cur.fetchone()
        apt_id = res["appointment_id"]
        apt_code = res["appointment_code"]

        if payload.items:
            items_data = [(apt_id, it.item_id, it.pet_id) for it in payload.items]
            execute_batch(cur, """
                INSERT INTO appointment_items (appointment_id, item_id, pet_id)
                VALUES (%s, %s, %s);
            """, items_data)

        conn.commit()
        return {"status": "success", "appointment_id": apt_id, "appointment_code": apt_code}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

class UpdateAppointmentStatusSchema(BaseModel):
    status: str

@app.patch("/api/appointments/{appointment_id}/status")
def update_appointment_status(appointment_id: int, payload: UpdateAppointmentStatusSchema):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE appointments
            SET status = %s
            WHERE appointment_id = %s
            RETURNING appointment_id, status;
        """, (payload.status, appointment_id))
        res = format_row(cur.fetchone())
        if not res:
            raise HTTPException(status_code=404, detail="Không tìm thấy lịch hẹn")
        conn.commit()
        return {"status": "success", "appointment": res}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.get("/api/appointments/{appointment_id}")
def get_appointment_detail(appointment_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 
                a.appointment_id,
                a.appointment_code,
                a.appointment_date,
                a.status,
                a.notes,
                c.full_name AS customer_name,
                c.phone_number
            FROM appointments a
            LEFT JOIN customers c ON a.customer_id = c.customer_id
            WHERE a.appointment_id = %s;
        """, (appointment_id,))
        apt = format_row(cur.fetchone())
        if not apt:
            raise HTTPException(status_code=404, detail="Không tìm thấy lịch hẹn")

        cur.execute("""
            SELECT 
                ai.appointment_item_id,
                p.name AS pet_name,
                p.species AS pet_species,
                p.breed AS pet_breed,
                sp.item_name,
                sp.base_price
            FROM appointment_items ai
            LEFT JOIN pets p ON ai.pet_id = p.pet_id
            LEFT JOIN services_products sp ON ai.item_id = sp.item_id
            WHERE ai.appointment_id = %s;
        """, (appointment_id,))
        apt["items"] = [format_row(r) for r in cur.fetchall()]
        return apt
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

# ==========================================
# 7. ORDERS & POS (CÓ PHÂN TRANG)
# ==========================================
class OrderItemSchema(BaseModel):
    item_id: int
    pet_id: Optional[int] = None
    item_name: str
    quantity: int
    unit_price: float
    subtotal: float
    notes: Optional[str] = None

class CreateOrderSchema(BaseModel):
    branch_id: int = 1
    staff_id: Optional[int] = 1
    customer_id: Optional[int] = None
    appointment_id: Optional[int] = None
    total_amount: float
    discount_amount: float = 0
    final_amount: float
    payment_method: str = "Cash"
    payment_status: str = "Completed"
    order_status: str = "Completed"
    notes: Optional[str] = None
    items: List[OrderItemSchema]

@app.post("/api/pos/orders")
def create_order(order_data: CreateOrderSchema):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO orders (
                branch_id, staff_id, customer_id, appointment_id,
                total_amount, discount_amount, final_amount,
                payment_method, payment_status, order_status, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING order_id, order_code;
        """, (
            order_data.branch_id, order_data.staff_id, order_data.customer_id, order_data.appointment_id,
            order_data.total_amount, order_data.discount_amount, order_data.final_amount,
            order_data.payment_method, order_data.payment_status, order_data.order_status, order_data.notes
        ))
        order_res = cur.fetchone()
        new_order_id = order_res["order_id"]
        order_code = order_res["order_code"]

        if order_data.items:
            items_payload = [
                (new_order_id, it.item_id, it.pet_id, it.item_name, it.quantity, it.unit_price, it.subtotal, it.notes)
                for it in order_data.items
            ]
            execute_batch(cur, """
                INSERT INTO order_items (
                    order_id, item_id, pet_id, item_name, quantity, unit_price, subtotal, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """, items_payload)

            stock_updates = [(it.quantity, it.item_id) for it in order_data.items]
            execute_batch(cur, """
                UPDATE services_products 
                SET stock_quantity = stock_quantity - %s 
                WHERE item_id = %s AND item_type = 'PRODUCT';
            """, stock_updates)

        if order_data.customer_id:
            cur.execute("""
                UPDATE customers
                SET tier = CASE 
                    WHEN sub.total >= 60000000 THEN 'Platinum'
                    WHEN sub.total >= 30000000 THEN 'Gold'
                    WHEN sub.total >= 6000000  THEN 'Silver'
                    ELSE 'Standard'
                END
                FROM (
                    SELECT COALESCE(SUM(final_amount), 0) AS total 
                    FROM orders 
                    WHERE customer_id = %s AND payment_status = 'Completed'
                ) sub
                WHERE customers.customer_id = %s;
            """, (order_data.customer_id, order_data.customer_id))
        
        if order_data.appointment_id:
            cur.execute("""
                UPDATE appointments 
                SET status = 'Completed' 
                WHERE appointment_id = %s;
            """, (order_data.appointment_id,))
            
        conn.commit()
        return {"status": "success", "order_id": new_order_id, "order_code": order_code}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.get("/api/orders")
def get_orders(branch_id: Optional[int] = None, limit: int = 50, offset: int = 0):
    conn = get_connection()
    cur = conn.cursor()
    try:
        query = """
            SELECT 
                o.order_id, 
                o.order_code, 
                o.final_amount, 
                o.payment_method, 
                o.payment_status, 
                o.order_status, 
                o.branch_id,
                o.created_at,
                c.full_name AS customer_name, 
                c.phone_number
            FROM orders o
            LEFT JOIN customers c ON o.customer_id = c.customer_id
        """
        params = []
        if branch_id:
            query += " WHERE o.branch_id = %s"
            params.append(branch_id)

        query += " ORDER BY o.created_at DESC LIMIT %s OFFSET %s;"
        params.extend([limit, offset])

        cur.execute(query, tuple(params))
        orders = [format_row(r) for r in cur.fetchall()]

        count_query = "SELECT COUNT(*) AS total FROM orders"
        if branch_id:
            count_query += " WHERE branch_id = %s"
            cur.execute(count_query, (branch_id,))
        else:
            cur.execute(count_query)
        total_count = cur.fetchone()["total"]

        return {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "orders": orders
        }
    finally:
        cur.close()
        conn.close()

@app.get("/api/orders/{order_id}")
def get_order_detail(order_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 
                o.order_id, o.order_code, o.total_amount, o.discount_amount, o.final_amount,
                o.payment_method, o.payment_status, o.order_status, o.created_at, o.notes,
                c.full_name AS customer_name, c.phone_number
            FROM orders o
            LEFT JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_id = %s;
        """, (order_id,))
        order = format_row(cur.fetchone())
        if not order:
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

        cur.execute("""
            SELECT 
                oi.order_item_id, oi.item_id, oi.item_name, oi.quantity, 
                oi.unit_price, oi.subtotal, oi.notes,
                p.name AS pet_name, p.species AS pet_species,
                sp.item_type
            FROM order_items oi
            LEFT JOIN pets p ON oi.pet_id = p.pet_id
            LEFT JOIN services_products sp ON oi.item_id = sp.item_id
            WHERE oi.order_id = %s
            ORDER BY oi.order_item_id ASC;
        """, (order_id,))
        order["items"] = [format_row(r) for r in cur.fetchall()]
        return order
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
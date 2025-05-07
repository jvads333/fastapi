from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from typing import List, Optional, Dict
from enum import Enum

class User(BaseModel):
    id: int
    name: str
    balance: float

class Loan(BaseModel):
    user_id: int
    amount: float

class TransactionType(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"

class Transaction(BaseModel):
    type: TransactionType
    amount: float

class BalanceResponse(BaseModel):
    user_id: int
    name: str
    current_balance: float
    loan_details: Optional[Loan] = None

users_db: Dict[int, User] = {}
loans_db: Dict[int, Loan] = {}

app = FastAPI()

def find_user_by_id(user_id: int) -> Optional[User]:
    return users_db.get(user_id)

def find_loan_by_user_id(user_id: int) -> Optional[Loan]:
    return loans_db.get(user_id)

@app.post("/users", response_model=User, status_code=201)
async def create_user(user_input: User = Body(...)):
    if user_input.id in users_db:
        raise HTTPException(status_code=400, detail=f"User with ID {user_input.id} already exists.")
    new_user = User(id=user_input.id, name=user_input.name, balance=0.0)
    users_db[new_user.id] = new_user
    return new_user

@app.post("/users/{user_id}/transaction", response_model=User)
async def perform_transaction(
    user_id: int = Path(..., title="The ID of the user", ge=1),
    transaction: Transaction = Body(...)
):
    user = find_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found.")
    if transaction.amount <= 0:
        raise HTTPException(status_code=400, detail="Transaction amount must be positive.")

    if transaction.type == TransactionType.DEBIT:
        if user.balance < transaction.amount:
            raise HTTPException(status_code=400, detail="Insufficient funds for debit.")
        user.balance -= transaction.amount
    elif transaction.type == TransactionType.CREDIT:
        user.balance += transaction.amount
    users_db[user_id] = user
    return user

@app.post("/users/{user_id}/loan", response_model=Loan, status_code=201)
async def take_loan(
    user_id: int = Path(..., title="The ID of the user", ge=1),
    loan_amount: float = Body(..., gt=0, embed=True, alias="amount")
):
    user = find_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found.")
    if find_loan_by_user_id(user_id):
        raise HTTPException(status_code=400, detail=f"User {user_id} already has an active loan.")

    transaction_to_perform = Transaction(type=TransactionType.CREDIT, amount=loan_amount)
    try:
        updated_user = await perform_transaction(user_id=user_id, transaction=transaction_to_perform)
    except HTTPException as e:
        raise e
    except Exception as e:
        error_detail = f"Unexpected error processing transaction for loan: {str(e)}"
        raise HTTPException(status_code=500, detail=error_detail)

    new_loan = Loan(user_id=user_id, amount=loan_amount)
    loans_db[user_id] = new_loan
    return new_loan

@app.get("/users/{user_id}/_internal_loan_info", response_model=Optional[Loan], include_in_schema=False)
async def get_internal_loan_info(user_id: int = Path(..., ge=1)):
    return find_loan_by_user_id(user_id)

@app.get("/users/{user_id}/balance", response_model=BalanceResponse)
async def get_user_balance_and_loan(
    user_id: int = Path(..., title="The ID of the user to check", ge=1)
):
    user = find_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found.")

    try:
        loan_details: Optional[Loan] = await get_internal_loan_info(user_id=user_id)
    except Exception as e:
        print(f"Warning: Failed to fetch loan details directly. Error: {str(e)}")
        loan_details = None

    return BalanceResponse(
        user_id=user.id,
        name=user.name,
        current_balance=user.balance,
        loan_details=loan_details
    )

@app.get("/users", response_model=List[User])
async def get_all_users():
    return list(users_db.values())

@app.get("/loans", response_model=List[Loan])
async def get_all_loans():
    return list(loans_db.values())
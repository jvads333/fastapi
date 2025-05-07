from fastapi import FastAPI, HTTPException, Path, Body
from fastapi.testclient import TestClient # To make internal HTTP calls
from pydantic import BaseModel
from typing import List, Optional, Dict
from enum import Enum

# --- Pydantic Models ---

class User(BaseModel):
    """
    A single model representing a user, including their ID, name, and balance.
    The balance is intended to be initialized by the application logic (e.g., to 0.0 on creation).
    """
    id: int
    name: str
    balance: float # For creation, this will be explicitly set to 0.0 by the endpoint

class Loan(BaseModel): # Model for a loan
    user_id: int
    amount: float

class TransactionType(str, Enum): # Enum for transaction types
    DEBIT = "debit"
    CREDIT = "credit"

class Transaction(BaseModel): # Model for a transaction
    type: TransactionType
    amount: float

class BalanceResponse(BaseModel): # Model for the response when fetching balance
    user_id: int
    name: str
    current_balance: float
    loan_details: Optional[Loan] = None # Loan details are optional

# --- In-memory "Databases" (Using Dictionaries for easier lookups) ---
users_db: Dict[int, User] = {} # Dictionary to store users (using the new User model), keyed by user_id
loans_db: Dict[int, Loan] = {} # Dictionary to store loans, keyed by user_id for easy lookup

# --- FastAPI App Instance ---
app = FastAPI(
    title="Simple Banking API (Consolidated User Model)",
    description="An API to manage users, balances, and loans, with a consolidated User model and internal calls.",
    version="1.3.0" # Incremented version
)

# --- Helper Functions (Simplified with Dicts) ---
def find_user_by_id(user_id: int) -> Optional[User]:
    # Retrieves a user from users_db by their ID, returns None if not found.
    return users_db.get(user_id)

def find_loan_by_user_id(user_id: int) -> Optional[Loan]:
    # Retrieves a loan from loans_db by user_ID, returns None if not found.
    return loans_db.get(user_id)

# --- API Endpoints ---

@app.post("/users", response_model=User, status_code=201)
async def create_user(user_input: User = Body(...)): # Input matches User model structure
    # Endpoint to create a new user.
    # user_input will have id, name, and balance (which defaults to 0.0 if not provided,
    # but we will explicitly set it to 0.0 for new users).
    if user_input.id in users_db: # Check if user ID already exists
        raise HTTPException(status_code=400, detail=f"User with ID {user_input.id} already exists.")
    
    # Explicitly create the User object to store, ensuring balance is 0.0,
    # regardless of what 'balance' value might have been sent in user_input.
    new_user = User(id=user_input.id, name=user_input.name, balance=0.0)
    
    users_db[new_user.id] = new_user # Add the new user to the in-memory database
    return new_user # Return the created user object (with balance 0.0)

@app.post("/users/{user_id}/transaction", response_model=User)
async def perform_transaction(
    user_id: int = Path(..., title="The ID of the user", ge=1), # User ID from path, must be >= 1
    transaction: Transaction = Body(...) # Transaction details from request body
):
    # Endpoint to perform a debit or credit transaction for a user.
    user = find_user_by_id(user_id) # Find the user by ID
    if not user: # If user not found
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found.")
    if transaction.amount <= 0: # Transaction amount must be positive
        raise HTTPException(status_code=400, detail="Transaction amount must be positive.")

    if transaction.type == TransactionType.DEBIT: # If it's a debit transaction
        if user.balance < transaction.amount: # Check for sufficient funds
            raise HTTPException(status_code=400, detail="Insufficient funds for debit.")
        user.balance -= transaction.amount # Subtract amount from balance
    elif transaction.type == TransactionType.CREDIT: # If it's a credit transaction
        user.balance += transaction.amount # Add amount to balance
    users_db[user_id] = user # Update the user's record in the database
    return user # Return the updated user object

@app.post("/users/{user_id}/loan", response_model=Loan, status_code=201)
async def take_loan(
    user_id: int = Path(..., title="The ID of the user", ge=1), # User ID from path
    loan_amount: float = Body(..., gt=0, embed=True, alias="amount") # Loan amount from body, must be > 0
):
    # Endpoint for a user to take out a loan.
    user = find_user_by_id(user_id) # Find the user
    if not user: # If user not found
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found.")
    if find_loan_by_user_id(user_id): # Check if a loan already exists for this user
        raise HTTPException(status_code=400, detail=f"User {user_id} already has an active loan.")

    # --- INTERNAL API CALL to update balance ---
    with TestClient(app) as client: # Create a TestClient instance for internal calls
        transaction_payload = {"type": TransactionType.CREDIT.value, "amount": loan_amount}
        transaction_url = f"/users/{user_id}/transaction"
        response = client.post(transaction_url, json=transaction_payload)

        if response.status_code != 200: # Check if the internal call was successful
            error_detail = f"Failed to credit loan amount to balance. Status: {response.status_code}."
            try:
                error_detail += f" Detail: {response.json().get('detail', response.text)}"
            except Exception:
                error_detail += f" Response: {response.text}"
            raise HTTPException(status_code=500, detail=error_detail)
    # --- End of INTERNAL API CALL ---

    new_loan = Loan(user_id=user_id, amount=loan_amount) # Create a new loan object
    loans_db[user_id] = new_loan # Store the loan
    return new_loan # Return the created loan object

@app.get("/users/{user_id}/_internal_loan_info", response_model=Optional[Loan], include_in_schema=False)
async def get_internal_loan_info(user_id: int = Path(..., ge=1)):
    # Internal endpoint to fetch loan information.
    return find_loan_by_user_id(user_id)

@app.get("/users/{user_id}/balance", response_model=BalanceResponse)
async def get_user_balance_and_loan(
    user_id: int = Path(..., title="The ID of the user to check", ge=1) # User ID from path
):
    # Endpoint to get a user's balance and loan details.
    user = find_user_by_id(user_id) # Find the user by ID
    if not user: # If user not found
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found.")

    loan_details: Optional[Loan] = None # Initialize loan_details

    # --- INTERNAL API CALL to get loan details ---
    with TestClient(app) as client: # Create a TestClient instance
        internal_loan_url = f"/users/{user_id}/_internal_loan_info"
        response = client.get(internal_loan_url) # Make the GET request

        if response.status_code == 200: # If the internal call was successful
            loan_data = response.json()
            if loan_data: # Check if loan_data is not null
                loan_details = Loan(**loan_data)
        else:
            print(f"Warning: Failed to fetch loan details internally. Status: {response.status_code}. Response: {response.text}")
            loan_details = None # Proceed without loan details if internal call fails
    # --- End of INTERNAL API CALL ---

    return BalanceResponse(
        user_id=user.id,
        name=user.name, # User object now directly has name
        current_balance=user.balance,
        loan_details=loan_details
    )

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

    # --- DIRECT FUNCTION CALL to update balance ---
    transaction_to_perform = Transaction(type=TransactionType.CREDIT, amount=loan_amount)
    try:
        # Directly call the perform_transaction async function
        # We need to await it as it's an async function
        updated_user = await perform_transaction(user_id=user_id, transaction=transaction_to_perform)
        # No need to check response.status_code, if perform_transaction fails,
        # it will raise an HTTPException which will propagate.
        # We can add a check on updated_user if necessary, but perform_transaction
        # already handles its own errors by raising HTTPExceptions.
    except HTTPException as e:
        # If perform_transaction raised an HTTPException, we can re-raise it
        # or wrap it in a more specific error for the loan process.
        # For simplicity, let's propagate it, but you could customize.
        # Example of wrapping:
        # raise HTTPException(status_code=500, detail=f"Failed to credit loan to balance: {e.detail}")
        raise e # Propagate the original HTTPException
    except Exception as e:
        # Catch any other unexpected Python errors during the direct call
        # and convert to a generic server error.
        error_detail = f"Unexpected error processing transaction for loan: {str(e)}"
        raise HTTPException(status_code=500, detail=error_detail)
    # --- End of DIRECT FUNCTION CALL ---

    new_loan = Loan(user_id=user_id, amount=loan_amount)
    loans_db[user_id] = new_loan
    return new_loan

@app.get("/users/{user_id}/_internal_loan_info", response_model=Optional[Loan], include_in_schema=False)
async def get_internal_loan_info(user_id: int = Path(..., ge=1)):
    # This function's logic remains the same, it's just called directly now.
    # We need to ensure the user_id path parameter validation (`ge=1`) is respected
    # if this endpoint were to be called externally. When called directly,
    # the caller is responsible for providing a valid user_id.
    # However, since get_user_balance_and_loan already validates its user_id,
    # this specific direct call is relatively safe.
    return find_loan_by_user_id(user_id)

@app.get("/users/{user_id}/balance", response_model=BalanceResponse)
async def get_user_balance_and_loan(
    user_id: int = Path(..., title="The ID of the user to check", ge=1)
):
    user = find_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found.")

    # --- DIRECT FUNCTION CALL to get loan details ---
    try:
        # Directly call the get_internal_loan_info async function
        # We need to await it as it's an async function
        loan_details: Optional[Loan] = await get_internal_loan_info(user_id=user_id)
    except Exception as e:
        # Handle potential errors from get_internal_loan_info, though it's unlikely
        # in its current simple form. This is more for robust error handling.
        print(f"Warning: Failed to fetch loan details directly. Error: {str(e)}")
        loan_details = None # Proceed without loan details if direct call fails
    # --- End of DIRECT FUNCTION CALL ---

    return BalanceResponse(
        user_id=user.id,
        name=user.name,
        current_balance=user.balance,
        loan_details=loan_details
    )


@app.get("/users", response_model=List[User], tags=["Admin"]) # response_model uses the new User
async def get_all_users():
    # Endpoint to retrieve a list of all users.
    return list(users_db.values())

@app.get("/loans", response_model=List[Loan], tags=["Admin"])
async def get_all_loans():
    # Endpoint to retrieve a list of all active loans.
    return list(loans_db.values())

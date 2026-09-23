"""
Example usage of the BaseRouter class.

This module demonstrates how to use the BaseRouter class with its new features.
"""
from fastapi import FastAPI
from typing import Dict, List, Optional
from pydantic import BaseModel
from app.core.base_router import BaseRouter


# Define some models for the example
class Item(BaseModel):
    """Example item model."""
    id: int
    name: str
    description: Optional[str] = None
    price: float
    tags: List[str] = []


class ItemCreate(BaseModel):
    """Model for creating an item."""
    name: str
    description: Optional[str] = None
    price: float
    tags: List[str] = []


# Create a FastAPI app
app = FastAPI(title="BaseRouter Example")


# Example 1: Basic usage with auto-derived service_name
items_router = BaseRouter(prefix="/items")

# In-memory database for the example
items_db = {
    1: Item(id=1, name="Hammer", price=9.99, tags=["tools", "hardware"]),
    2: Item(id=2, name="Screwdriver", price=4.99, tags=["tools", "hardware"]),
    3: Item(id=3, name="Wrench", price=7.99, tags=["tools", "hardware"])
}


@items_router.get("/", response_model=List[Item])
async def get_items():
    """Get all items."""
    return list(items_db.values())


@items_router.get("/{item_id}", response_model=Item)
async def get_item(item_id: int):
    """Get a specific item by ID."""
    if item_id not in items_db:
        items_router.raise_not_found_error("Item", item_id)
    return items_db[item_id]


@items_router.post("/", response_model=Item, status_code=201)
async def create_item(item: ItemCreate):
    """Create a new item."""
    # Generate a new ID
    new_id = max(items_db.keys()) + 1 if items_db else 1
    
    # Create the item
    new_item = Item(id=new_id, **item.dict())
    
    # Add to database
    items_db[new_id] = new_item
    
    return new_item


# Example 2: Using explicit service_name and custom responses
custom_responses = {
    400: {
        "description": "Bad Request",
        "content": {
            "application/problem+json": {
                "example": {
                    "type": "https://headwater.com/problems/validation_error",
                    "title": "Validation Error",
                    "status": 400,
                    "detail": "Invalid product data",
                    "fields": ["name", "price"]
                }
            }
        }
    },
    404: {
        "description": "Product Not Found",
        "content": {
            "application/problem+json": {
                "example": {
                    "type": "https://headwater.com/problems/not_found",
                    "title": "Product Not Found",
                    "status": 404,
                    "detail": "The requested product does not exist",
                    "product_id": "123"
                }
            }
        }
    }
}

products_router = BaseRouter(
    prefix="/api/v1/products",
    service_name="product-catalog",
    responses=custom_responses
)


# Example product database
products_db = {
    "p1": {"id": "p1", "name": "Laptop", "price": 999.99, "in_stock": True},
    "p2": {"id": "p2", "name": "Smartphone", "price": 499.99, "in_stock": True},
    "p3": {"id": "p3", "name": "Headphones", "price": 99.99, "in_stock": False}
}


@products_router.get("/")
async def get_products(in_stock: Optional[bool] = None):
    """Get all products, optionally filtered by in_stock status."""
    if in_stock is None:
        return list(products_db.values())
    
    return [p for p in products_db.values() if p["in_stock"] == in_stock]


@products_router.get("/{product_id}")
async def get_product(product_id: str):
    """Get a specific product by ID."""
    if product_id not in products_db:
        products_router.raise_http_exception(
            status_code=404,
            detail=f"Product with ID {product_id} not found",
            type="product_not_found",
            product_id=product_id
        )
    return products_db[product_id]


@products_router.post("/")
async def create_product(product: Dict):
    """Create a new product."""
    # Validate required fields
    if "name" not in product or "price" not in product:
        products_router.raise_validation_error(
            "Missing required fields",
            fields=["name", "price"]
        )
    
    # Generate a new ID
    new_id = f"p{len(products_db) + 1}"
    
    # Create the product
    new_product = {
        "id": new_id,
        "name": product["name"],
        "price": product["price"],
        "in_stock": product.get("in_stock", True)
    }
    
    # Add to database
    products_db[new_id] = new_product
    
    return new_product


# Include the routers in the app
app.include_router(items_router())
app.include_router(products_router())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

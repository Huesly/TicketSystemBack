import argparse
import asyncio
import os
import sys
from pathlib import Path

# Asegurar que la raíz del proyecto esté en sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import get_password_hash
from app.models.user import Profile

async def reset_password(email: str, new_password: str):
    async with AsyncSessionLocal() as session:
        clean_email = email.lower().strip()
        stmt = select(Profile).where(Profile.email == clean_email)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()
        if not user:
            print(f"ERROR: No se encontró ningún usuario con el correo: '{clean_email}'")
            sys.exit(1)
        
        user.hashed_password = get_password_hash(new_password)
        await session.commit()
        print(f"OK: La contraseña de '{user.full_name}' ({user.email}) ha sido actualizada exitosamente.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Restablecer contraseña de un usuario o administrador")
    parser.add_argument("--email", required=True, help="Correo electrónico del usuario")
    parser.add_argument("--password", required=True, help="Nueva contraseña en texto plano")
    args = parser.parse_args()

    if len(args.password) < 6:
        print("ERROR: La contraseña debe tener al menos 6 caracteres.")
        sys.exit(1)

    asyncio.run(reset_password(args.email, args.password))

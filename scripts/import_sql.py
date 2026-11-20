#!/usr/bin/env python3
# =============================================================================
# IMPORTADOR TRANSACCIONAL SEGURO DE INPUTS SQL (CLI + SERVICIO BACKEND)
# Uso CLI:
#   docker exec confytickets-backend python /app/scripts/import_sql.py /ruta/archivo.sql
#   docker exec confytickets-backend python /app/scripts/import_sql.py --sql "SELECT count(*) FROM profiles;"
# =============================================================================

import sys
import re
import asyncio
import argparse
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import AsyncSessionLocal

# Comandos destructivos o de escalamiento de privilegios bloqueados por seguridad
FORBIDDEN_SQL_PATTERNS = [
    (re.compile(r"\bDROP\s+(DATABASE|SCHEMA|ROLE|USER|OWNED)\b", re.IGNORECASE), "Eliminación de base de datos, esquema o rol no permitida."),
    (re.compile(r"\bTRUNCATE\s+TABLE\s+(profiles|businesses|loyalty_programs)\b", re.IGNORECASE), "Truncado de tablas maestras bloqueado por seguridad."),
    (re.compile(r"\bALTER\s+(SYSTEM|ROLE|USER|DATABASE)\b", re.IGNORECASE), "Alteración de configuración del motor PostgreSQL no permitida."),
    (re.compile(r"\bCOPY\b[\s\S]*?\bPROGRAM\b", re.IGNORECASE), "Ejecución de comandos de sistema vía COPY PROGRAM bloqueada."),
    (re.compile(r"\bpg_read_file\b|\bpg_ls_dir\b|\blo_import\b|\blo_export\b", re.IGNORECASE), "Funciones de lectura/escritura de sistema de archivos bloqueadas."),
]


def split_sql_statements(sql_script: str) -> List[str]:
    """
    Divide un bloque o archivo SQL en sentencias individuales, ignorando comentarios
    (-- y /* */) y respetando cadenas entre comillas simples.
    """
    # Eliminar comentarios de bloque /* ... */
    cleaned = re.sub(r"/\*[\s\S]*?\*/", "", sql_script)
    # Eliminar comentarios de línea --
    lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        # Quitar comentario al final de línea si no hay comillas
        if "--" in line and "'" not in line:
            line = line.split("--", 1)[0]
        lines.append(line)

    full_text = "\n".join(lines)
    statements: List[str] = []
    current: List[str] = []
    in_single_quote = False

    i = 0
    while i < len(full_text):
        ch = full_text[i]
        if ch == "'":
            # Manejar escape '' en SQL
            if in_single_quote and i + 1 < len(full_text) and full_text[i + 1] == "'":
                current.append("''")
                i += 2
                continue
            in_single_quote = not in_single_quote
            current.append(ch)
        elif ch == ";" and not in_single_quote:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1

    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)

    return statements


def validate_sql_safety(statements: List[str]) -> None:
    """Valida que ninguna sentencia contenga instrucciones destructivas prohibidas."""
    for idx, stmt in enumerate(statements, start=1):
        for pattern, reason in FORBIDDEN_SQL_PATTERNS:
            if pattern.search(stmt):
                raise ValueError(f"Sentencia #{idx} rechazada por política de seguridad: {reason}")


async def execute_safe_sql_batch(
    db: AsyncSession,
    sql_script: str,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Ejecuta un conjunto de sentencias SQL dentro de una transacción segura.
    Si dry_run=True, ejecuta y valida todo pero hace ROLLBACK al final.
    Si ocurre cualquier error en una sentencia, revierte toda la transacción (ATOMICIDAD).
    """
    statements = split_sql_statements(sql_script)
    if not statements:
        raise ValueError("No se encontraron sentencias SQL válidas para ejecutar.")

    if len(statements) > 200:
        raise ValueError("Por seguridad, cada lote está limitado a máximo 200 sentencias SQL.")

    validate_sql_safety(statements)

    results: List[Dict[str, Any]] = []
    total_rows_affected = 0

    try:
        for idx, stmt in enumerate(statements, start=1):
            first_word = stmt.split()[0].upper() if stmt.split() else "SQL"
            res = await db.execute(text(stmt))

            if res.returns_rows:
                rows = [dict(r._mapping) for r in res.fetchall()[:50]]
                # Convertir UUIDs y fechas a string para serialización JSON limpia
                serializable_rows = [
                    {k: (str(v) if v is not None and not isinstance(v, (int, float, bool, str)) else v) for k, v in row.items()}
                    for row in rows
                ]
                results.append({
                    "index": idx,
                    "command": first_word,
                    "statement_preview": stmt[:120] + ("..." if len(stmt) > 120 else ""),
                    "rows_returned": len(serializable_rows),
                    "sample_rows": serializable_rows
                })
            else:
                affected = res.rowcount if res.rowcount and res.rowcount > 0 else 0
                total_rows_affected += affected
                results.append({
                    "index": idx,
                    "command": first_word,
                    "statement_preview": stmt[:120] + ("..." if len(stmt) > 120 else ""),
                    "rows_affected": affected
                })

        if dry_run:
            await db.rollback()
            status_msg = "Simulación (Dry-Run) exitosa. Todos los cambios fueron revertidos (ROLLBACK)."
        else:
            await db.commit()
            status_msg = "Lote SQL importado y confirmado exitosamente (COMMIT)."

        return {
            "status": "dry_run_ok" if dry_run else "committed",
            "message": status_msg,
            "statements_executed": len(statements),
            "total_rows_affected": total_rows_affected,
            "details": results
        }
    except Exception as exc:
        await db.rollback()
        raise RuntimeError(f"Error en ejecución SQL (Transacción revertida con ROLLBACK): {str(exc)}")


async def main_cli():
    parser = argparse.ArgumentParser(description="Importador seguro de inputs SQL para ConfyTickets")
    parser.add_argument("file", nargs="?", help="Ruta al archivo .sql a importar")
    parser.add_argument("--sql", help="Sentencia(s) SQL directas entre comillas")
    parser.add_argument("--dry-run", action="store_true", help="Valida y simula sin guardar cambios (ROLLBACK)")
    args = parser.parse_args()

    sql_content = ""
    if args.sql:
        sql_content = args.sql
    elif args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"[ERROR] El archivo '{args.file}' no existe.")
            sys.exit(1)
        sql_content = path.read_text(encoding="utf-8")
    else:
        print("[ERROR] Especifica un archivo .sql o usa --sql \"SENTENCIA;\"")
        sys.exit(1)

    async with AsyncSessionLocal() as session:
        try:
            report = await execute_safe_sql_batch(session, sql_content, dry_run=args.dry_run)
            print("=" * 72)
            print(f" ESTADO       : {report['status'].upper()}")
            print(f" MENSAJE      : {report['message']}")
            print(f" SENTENCIAS   : {report['statements_executed']}")
            print(f" FILAS AFECT. : {report['total_rows_affected']}")
            print("=" * 72)
            for item in report["details"]:
                print(f" #{item['index']} [{item['command']}] -> {item['statement_preview']}")
                if "sample_rows" in item:
                    for r in item["sample_rows"][:10]:
                        print(f"    {r}")
                else:
                    print(f"    Filas afectadas: {item.get('rows_affected', 0)}")
        except Exception as e:
            print(f"[FALLO DE IMPORTACIÓN SQL] {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main_cli())

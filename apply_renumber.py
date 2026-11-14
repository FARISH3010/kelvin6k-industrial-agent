def add_renumber(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()

    target = '''    log.info("Deleted operator %s (%s)", real_id, op_name)
    return True'''

    renumber_func = '''def renumber_operators(db_path: Optional[str] = None) -> None:
    """
    Renumbers all operators starting from 1 to N sequentially,
    updating foreign key references across shifts, shift_issues,
    printing_jobs, attendance_records, and session_context,
    and resets sqlite_sequence so new inserts start immediately after N.
    """
    with db.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        ops = conn.execute("SELECT * FROM operators ORDER BY operator_id ASC").fetchall()
        id_map = {}
        for new_idx, op_row in enumerate(ops, start=1):
            old_id = op_row["operator_id"]
            if old_id != new_idx:
                id_map[old_id] = new_idx

        if id_map:
            # 1. Map to temporary negative IDs to avoid UNIQUE constraint violations
            for old_id, new_id in id_map.items():
                temp_id = -old_id
                conn.execute("UPDATE operators SET operator_id = ? WHERE operator_id = ?", (temp_id, old_id))
                conn.execute("UPDATE shifts SET operator_id = ? WHERE operator_id = ? OR CAST(operator_id AS TEXT) = ?", (temp_id, old_id, str(old_id)))
                conn.execute("UPDATE shift_issues SET operator_id = ? WHERE operator_id = ? OR CAST(operator_id AS TEXT) = ?", (temp_id, old_id, str(old_id)))
                try:
                    conn.execute("UPDATE printing_jobs SET operator_id = ? WHERE operator_id = ? OR CAST(operator_id AS TEXT) = ?", (temp_id, old_id, str(old_id)))
                except Exception:
                    pass
                try:
                    conn.execute("UPDATE attendance_records SET operator_id = ? WHERE operator_id = ? OR CAST(operator_id AS TEXT) = ?", (temp_id, old_id, str(old_id)))
                except Exception:
                    pass
                try:
                    conn.execute("UPDATE session_context SET current_operator_id = ? WHERE current_operator_id = ? OR CAST(current_operator_id AS TEXT) = ?", (temp_id, old_id, str(old_id)))
                except Exception:
                    pass

            # 2. Map from temporary negative IDs to new sequential IDs
            for old_id, new_id in id_map.items():
                temp_id = -old_id
                conn.execute("UPDATE operators SET operator_id = ? WHERE operator_id = ?", (new_id, temp_id))
                conn.execute("UPDATE shifts SET operator_id = ? WHERE operator_id = ? OR CAST(operator_id AS TEXT) = ?", (new_id, temp_id, str(temp_id)))
                conn.execute("UPDATE shift_issues SET operator_id = ? WHERE operator_id = ? OR CAST(operator_id AS TEXT) = ?", (new_id, temp_id, str(temp_id)))
                try:
                    conn.execute("UPDATE printing_jobs SET operator_id = ? WHERE operator_id = ? OR CAST(operator_id AS TEXT) = ?", (new_id, temp_id, str(temp_id)))
                except Exception:
                    pass
                try:
                    conn.execute("UPDATE attendance_records SET operator_id = ? WHERE operator_id = ? OR CAST(operator_id AS TEXT) = ?", (new_id, temp_id, str(temp_id)))
                except Exception:
                    pass
                try:
                    conn.execute("UPDATE session_context SET current_operator_id = ? WHERE current_operator_id = ? OR CAST(current_operator_id AS TEXT) = ?", (new_id, temp_id, str(temp_id)))
                except Exception:
                    pass

        # Reset sqlite_sequence to the highest assigned operator_id (or 0 if no operators left)
        max_row = conn.execute("SELECT MAX(operator_id) AS max_id FROM operators").fetchone()
        max_id = max_row["max_id"] if max_row and max_row["max_id"] is not None else 0
        try:
            conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'operators'", (max_id,))
        except Exception:
            pass
        conn.execute("PRAGMA foreign_keys = ON")
    log.info("Renumbers operators complete. Highest id: %s", max_id)
'''

    new_delete_end = '''        # 6. Delete operator parent record
        conn.execute(
            "DELETE FROM operators WHERE operator_id = ? OR CAST(operator_id AS TEXT) = ?",
            (real_id, str(real_id)),
        )

    # Renumber remaining operators so IDs start from 1 sequentially
    renumber_operators(db_path)

    log.info("Deleted operator %s (%s) and renumbered operators", real_id, op_name)
    return True'''

    old_delete_end = '''        # 6. Delete operator parent record
        conn.execute(
            "DELETE FROM operators WHERE operator_id = ? OR CAST(operator_id AS TEXT) = ?",
            (real_id, str(real_id)),
        )

    log.info("Deleted operator %s (%s)", real_id, op_name)
    return True'''

    if old_delete_end in text:
        text = text.replace(old_delete_end, renumber_func + "\n\n" + new_delete_end)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"Added renumber_operators and updated delete_operator in {filepath}")
    else:
        print(f"Could not find old_delete_end in {filepath}")

add_renumber(r'agent\shift_store.py')
add_renumber(r'agent1\agent\shift_store.py')

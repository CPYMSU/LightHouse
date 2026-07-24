from lighthouse.bootstrap import migration_sql


def test_migration_adds_desktop_kernel_contracts():
    sql = migration_sql()
    assert "desktop_target_id" in sql
    assert "'data','system','desktop'" in sql
    assert "'data','system','desktop','auto'" in sql

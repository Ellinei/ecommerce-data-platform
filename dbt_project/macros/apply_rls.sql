{% macro apply_rls_policy(policy_name, role, using_expr) %}
  -- Re-applies row-level security on every model run, since `table`
  -- materialization drops/recreates the relation each time (see
  -- grant_select.sql's advisory-lock note for the same class of issue).
  -- Enabling RLS is idempotent by nature; policy creation uses the same
  -- duplicate_object idiom as governance/setup_roles.sql.
  ALTER TABLE {{ this }} ENABLE ROW LEVEL SECURITY;

  DO $$ BEGIN
    CREATE POLICY {{ policy_name }}
      ON {{ this }}
      FOR SELECT TO {{ role }}
      USING ({{ using_expr }});
  EXCEPTION WHEN duplicate_object THEN NULL;
  END $$;
{% endmacro %}

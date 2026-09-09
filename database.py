import os
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Cliente normal
supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

# Cliente administrativo
# NUNCA enviar esta clave al navegador.
supabase_admin = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY
)

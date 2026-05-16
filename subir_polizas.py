import os
from app.database import supabase

# --- CONFIGURACIÓN ---
CARPETA_PDFS = r"C:\Ruta\A\Tu\Carpeta\De\PDFs" # <--- CAMBIA ESTO
BUCKET_NAME = "polizas"

def cargar_archivos():
    for archivo in os.listdir(CARPETA_PDFS):
        if archivo.endswith(".pdf"):
            ruta_completa = os.path.join(CARPETA_PDFS, archivo)
            
            print(f"Subiendo: {archivo}...")
            
            # 1. Subir al Storage
            with open(ruta_completa, 'rb') as f:
                supabase.storage.from_(BUCKET_NAME).upload(archivo, f)
            
            # 2. Sacar la URL pública
            url_publica = supabase.storage.from_(BUCKET_NAME).get_public_url(archivo)
            
            # 3. Guardar en la tabla 'polizas'
            # Asumimos que el nombre del archivo es el número de póliza
            datos = {
                "numero_poliza": archivo.replace(".pdf", ""),
                "url_archivo": url_publica,
                "estado": "activa"
            }
            supabase.table("polizas").insert(datos).execute()

    print("¡Proceso terminado!")

if __name__ == "__main__":
    cargar_archivos()
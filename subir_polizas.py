import os
from app.database import supabase

# --- CONFIGURACIÓN ---
CARPETA_PDFS = r"C:\Ruta\A\Tu\Carpeta\De\PDFs" 
BUCKET_NAME = "polizas"

def cargar_archivos():
    for archivo in os.listdir(CARPETA_PDFS):
        if archivo.endswith(".pdf"):
            ruta_completa = os.path.join(CARPETA_PDFS, archivo)
            
            # LÓGICA: Determinar carpeta según el nombre
            # Si el archivo tiene la palabra "RECIBO" en su nombre, va a la carpeta 'recibos'
            # Si no, asumimos que es 'polizas'
            carpeta = "recibos" if "RECIBO" in archivo.upper() else "polizas"
            ruta_en_storage = f"{carpeta}/{archivo}"
            
            print(f"Subiendo {archivo} a la carpeta '{carpeta}'...")
            
            # 1. Subir al Storage (ahora incluye la carpeta en la ruta)
            with open(ruta_completa, 'rb') as f:
                supabase.storage.from_(BUCKET_NAME).upload(ruta_en_storage, f)
            
            # 2. Sacar la URL pública
            url_publica = supabase.storage.from_(BUCKET_NAME).get_public_url(ruta_en_storage)
            
            # 3. Guardar en la tabla 'polizas'
            datos = {
                "numero_poliza": archivo.replace(".pdf", ""),
                "url_archivo": url_publica,
                "estado": "activa",
                "tipo": carpeta  # Guardamos si es poliza o recibo para saber dónde buscar luego
            }
            supabase.table("polizas").insert(datos).execute()

    print("¡Proceso terminado!")

if __name__ == "__main__":
    cargar_archivos()

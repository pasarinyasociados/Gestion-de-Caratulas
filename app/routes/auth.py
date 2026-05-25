import io
import os
import re
import zipfile
import tempfile
from datetime import datetime
from fastapi import APIRouter, Form, Request, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse, StreamingResponse, FileResponse
from app.database import supabase
from postgrest.exceptions import APIError
from concurrent.futures import ThreadPoolExecutor
from fastapi import Request, HTTPException, BackgroundTasks
from urllib.parse import urlparse, unquote
import unicodedata
from urllib.parse import unquote, urlparse

router = APIRouter()

@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    # 1. Traemos al usuario (ahora jalamos todo con "*" para incluir las nuevas columnas)
    user = supabase.table("perfiles").select("*").eq("username", username).execute()
    
    if not user.data:
        return RedirectResponse(url="/?error=Usuario+no+encontrado", status_code=303)
    
    perfil = user.data[0]
    
    # 2. Validamos la contraseña
    if perfil['password_hash'].strip() == password.strip():
        
        # 🔒 [NUEVO CANDADO] Revisa si este perfil está amarrado a la oficina
        if perfil.get("requiere_token") is True:
            # Leemos la marca que viene desde el navegador (en un momento vemos cómo la manda el HTML)
            token_navegador = request.headers.get("X-Token-Oficina")
            token_seguro_db = perfil.get("token_oficina")
            
            # Si el navegador está vacío o no coincide el token... ¡Bloqueado!
            if not token_navegador or token_navegador != token_seguro_db:
                return RedirectResponse(url="/?error=Equipo+no+autorizado+para+este+usuario", status_code=303)
        
        # 3. Si pasó el candado (o si es dueño y no requiere token), inicia sesión normal
        request.session["usuario_id"] = str(perfil['id'])
        request.session["usuario_rol"] = str(perfil['rol']).lower()
        request.session["usuario_nombre"] = str(perfil['username']) 
        return RedirectResponse(url="/panel", status_code=303)
    else:
        return RedirectResponse(url="/?error=Contrasena+incorrecta", status_code=303)
        
@router.get("/buscar_poliza")
def buscar_poliza(request: Request, cliente: str = None, dia: str = None, mes: str = None, anio: str = None, tipo: str = None):
    if not request.session.get("usuario_id"):
        raise HTTPException(status_code=401, detail="No autorizado")

    query = supabase.table("polizas").select("*")
    
    # --- BUSCADOR INTELIGENTE POR PALABRAS ---
    if cliente and cliente.strip() and cliente != "undefined":
        # Separamos lo que escribiste por espacios (Ej: ["juan", "perez"])
        palabras = cliente.strip().split()
        for palabra in palabras:
            # Obligamos a que CADA palabra exista en el nombre, sin importar el orden
            query = query.ilike("cliente_nombre", f"%{palabra}%")

    if dia and dia.strip() and dia != "undefined":
        query = query.eq("dia", int(dia))
    if mes and mes.strip() and mes != "undefined" and mes.upper() != "TODOS":
        query = query.eq("mes", mes.strip().upper())
    if anio and anio.strip() and anio != "undefined" and anio != "todos":
        query = query.eq("anio", int(anio))
        
    # --- FILTRO DE TIPO (Póliza, Recibo o Todos) ---
    if tipo and tipo.strip() and tipo != "undefined" and tipo.lower() != "todos":
        query = query.eq("tipo", tipo.strip().lower())
        
    resultado = query.execute()
    return resultado.data if resultado.data else {"error": "No se encontraron pólizas"}

@router.post("/subir_poliza")
async def subir_poliza(
    request: Request, 
    file: UploadFile = File(...), 
    anio: int = Form(...),
    tipo: str = Form("poliza")
):
    if not request.session.get("usuario_id"):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        nombre_original = file.filename.strip()
        
        # 1. Extraer día y mes (Ej: "04 MAY_...")
        dia_extraido = int(nombre_original[:2])
        mes_extraido = nombre_original[2:6].replace("_", "").strip().upper()
        
        # 2. Aislar la parte del cliente
        nombre_cliente = nombre_original.split("_", 1)[1] if "_" in nombre_original else nombre_original[6:]
        nombre_cliente = nombre_cliente.replace(".pdf", "").replace(".PDF", "").strip()
        
        # --- FILTRO INTELIGENTE: LIMPIEZA DE COLA (SOLO PARA BÚSQUEDAS EN DB) ---
        nombre_cliente_limpio = re.sub(r'_\d+.*$', '', nombre_cliente)
        nombre_cliente_limpio = nombre_cliente_limpio.replace("_", " ").strip()
        
        # Para mantener el nombre original del archivo sin la extensión .pdf en la descarga si se requiere
        nombre_archivo_base = nombre_original.replace(".pdf", "").replace(".PDF", "").strip()
        
        # Normalizar el nombre físico para el almacenamiento en Storage
        nombre_limpio_storage = unicodedata.normalize('NFKD', nombre_original).encode('ascii', 'ignore').decode('ascii')
        file_path = f"{anio}/{mes_extraido}/{tipo}/{nombre_limpio_storage}"

        # --- VALIDACIÓN ANTIDUPLICADOS REAL (POR PATH EXACTO DEL ARCHIVO) ---
        existe_poliza = supabase.table("polizas")\
            .select("id")\
            .eq("path_storage", file_path)\
            .execute()
            
        if existe_poliza.data:
            return {"error": "duplicado", "message": f"El archivo '{nombre_original}' ya existe en el sistema para este año."}

        # --- SI ES NUEVA, PROCEDEMOS CON LA SUBIDA FÍSICA ---
        file_content = await file.read()
        supabase.storage.from_("polizas").upload(
            path=file_path, 
            file=file_content, 
            file_options={"content-type": "application/pdf", "x-upsert": "true"}
        )
        url_archivo = supabase.storage.from_("polizas").get_public_url(file_path)
            
        data = {
            "cliente_nombre": nombre_cliente_limpio, 
            "dia": dia_extraido, 
            "mes": mes_extraido[:3], 
            "anio": anio, 
            "url_archivo": url_archivo,
            "path_storage": file_path, 
            "tipo": tipo
        }

        supabase.table("polizas").insert(data).execute()
        return {"message": "Exito"}
        
    except Exception as e:
        error_msg = str(e).lower()
        
        # Identificamos si es un error real de duplicado (conflicto 409)
        if "409" in error_msg or "already exists" in error_msg:
            return {"error": "duplicado", "message": "El archivo ya existe en el sistema."}
        
        # Identificamos si es un error de conexión o tiempo de espera (Timeout)
        elif "timed out" in error_msg or "read operation" in error_msg or "timeout" in error_msg:
            return {"error": "timeout", "message": "El servidor tardó mucho en responder (Timeout). Intenta subir este archivo de nuevo."}
        
        # Cualquier otro error técnico que llegue a pasar
        else:
            return {"error": "desconocido", "message": f"Error inesperado: {str(e)[:50]}"}
        
@router.get("/usuarios_lista")
def listar_usuarios(request: Request):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        raise HTTPException(status_code=401, detail="No autorizado")
    return supabase.table("perfiles").select("*").execute().data

@router.delete("/borrar_usuario/{user_id}")
def borrar_usuario(request: Request, user_id: str):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        raise HTTPException(status_code=401, detail="No autorizado")
    
    resultado = supabase.table("perfiles").delete().eq("id", user_id).execute()
    
    # Si 'data' viene vacío, significa que Supabase no tocó ninguna fila
    if not resultado.data:
        raise HTTPException(status_code=400, detail="Supabase no borró el registro. Revisa el RLS en tu panel.")
        
    return {"status": "success"}

@router.post("/crear_usuario")
def crear_usuario(request: Request, username: str = Form(...), password: str = Form(...), rol: str = Form(...)):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        return RedirectResponse(url="/?error=No+autorizado", status_code=303)
    rol_solicitado = rol.strip().lower()
    opciones_rol = [rol_solicitado] if rol_solicitado == "admin" else ["user", "usuario", "operador"]
    for r_intento in opciones_rol:
        try:
            nuevo = {"username": username.strip(), "password_hash": password.strip(), "rol": r_intento}
            supabase.table("perfiles").insert(nuevo).execute()
            return RedirectResponse(url="/usuarios_pantalla", status_code=303)
        except APIError:
            continue
    return RedirectResponse(url="/usuarios_pantalla?error=Error+al+crear", status_code=303)

@router.post("/actualizar_usuario/{user_id}")
def actualizar_usuario(request: Request, user_id: str, password: str = Form(...), rol: str = Form(...)):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        return RedirectResponse(url="/?error=No+autorizado", status_code=303)
    
    rol_solicitado = rol.strip().lower()
    opciones_rol = [rol_solicitado] if rol_solicitado == "admin" else ["user", "usuario", "operador"]
    
    for r_intento in opciones_rol:
        try:
            actualizacion = {"password_hash": password.strip(), "rol": r_intento}
            resultado = supabase.table("perfiles").update(actualizacion).eq("id", user_id).execute()
            
            # Si logró modificar la fila con éxito, redirecciona
            if resultado.data:
                return RedirectResponse(url="/usuarios_pantalla", status_code=303)
        except APIError:
            continue
            
    return RedirectResponse(url="/usuarios_pantalla?error=Supabase+no+modifico+nada+Revisa+RLS", status_code=303)

@router.get("/detectar_obsoleto")
def detectar_obsoleto(request: Request):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        raise HTTPException(status_code=401, detail="No autorizado")
    
    anio_actual = datetime.now().year
    limite_vigencia = anio_actual - 2
    resultado = supabase.table("polizas").select("anio").lt("anio", limite_vigencia).execute()
    
    if resultado.data:
        anios_obsoletos = sorted(list(set([p['anio'] for p in resultado.data])))
        return {"anios": [anios_obsoletos]}
    
    return {"anios": []}

@router.get("/descargar_ano/{anio}")
def descargar_anio(request: Request, anio: str, background_tasks: BackgroundTasks = BackgroundTasks()):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        raise HTTPException(status_code=404, detail="No autorizado")
    
    query = supabase.table("polizas").select("*")
    if anio != "todos":
        query = query.eq("anio", int(anio))
        
    polizas = query.execute()
    if not polizas.data:
        raise HTTPException(status_code=404, detail="No hay pólizas para descargar")
        
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    
    # Función interna para descargar archivos en paralelo
    def descargar_un_archivo(p):
        try:
            path_interno = p.get('path_storage')
            if not path_interno:
                url_path = urlparse(p['url_archivo']).path
                path_interno = url_path.split("/object/public/polizas/")[1]
                path_interno = unquote(path_interno)
            
            archivo_binario = supabase.storage.from_("polizas").download(path_interno)
            return {
                "anio": p['anio'],
                "mes": p['mes'],
                "id": p['id'],
                "nombre_archivo": path_interno.split("/")[-1],
                "binario": archivo_binario
            }
        except Exception as e:
            print(f"Error descargando de Supabase: {e}")
            return None

    # Descargamos hasta 10 PDFs al mismo tiempo
    with ThreadPoolExecutor(max_workers=10) as executor:
        resultados = list(executor.map(descargar_un_archivo, polizas.data))
    
    # Armamos el ZIP al instante sin comprimir (ZIP_STORED) porque los PDFs ya vienen listos
    nombres_usados = set()
    with zipfile.ZipFile(temp_file.name, "w", zipfile.ZIP_STORED) as zip_file:
        for res in resultados:
            if not res:
                continue
            
            ruta_interna = f"{res['anio']}/{res['mes']}/{res['nombre_archivo']}"
            
            if ruta_interna in nombres_usados:
                nombre_base, ext = os.path.splitext(res['nombre_archivo'])
                ruta_interna = f"{res['anio']}/{res['mes']}/{nombre_base}_{res['id']}{ext}"
            
            nombres_usados.add(ruta_interna)
            zip_file.writestr(ruta_interna, res['binario'])
                
    background_tasks.add_task(os.remove, temp_file.name)
    
    nombre_zip = f"polizas_{anio}.zip" if anio != "todos" else "respaldo_total.zip"
    return FileResponse(path=temp_file.name, filename=nombre_zip, media_type="application/zip")
    
@router.delete("/purgar_ano/{anio}")
def purgar_anio(request: Request, anio: int):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        raise HTTPException(status_code=401, detail="No autorizado")
        
    polizas = supabase.table("polizas").select("*").eq("anio", anio).execute()
    if not polizas.data:
        return {"message": "No había registros para eliminar."}
        
    archivos_a_borrar = []
    for p in polizas.data:
        try:
            path_interno = p.get('path_storage')
            if not path_interno:
                url_path = urlparse(p['url_archivo']).path
                path_interno = url_path.split("/object/public/polizas/")[1]
                path_interno = unquote(path_interno)
            archivos_a_borrar.append(path_interno)
        except:
            continue
        
    if archivos_a_borrar:
        try:
            supabase.storage.from_("polizas").remove(archivos_a_borrar)
        except Exception as e:
            print(f"Error borrando storage: {e}")
            
    supabase.table("polizas").delete().eq("anio", anio).execute()
    return {"status": "success", "message": f"Año {anio} depurado por completo."}

@router.delete("/eliminar_poliza/{poliza_id}")
def eliminar_poliza(request: Request, poliza_id: str, url_archivo: str = None):
    if not request.session.get("usuario_id"):
        raise HTTPException(status_code=401, detail="No autorizado")
    try:
        if not url_archivo:
            poliza = supabase.table("polizas").select("url_archivo").eq("id", poliza_id).execute()
            if poliza.data:
                url_archivo = poliza.data[0]['url_archivo']
        
        if url_archivo:
            try:
                url_path = urlparse(url_archivo).path
                path_interno = url_path.split("/object/public/polizas/")[1]
                path_interno = unquote(unquote(path_interno))
                
                supabase.storage.from_("polizas").remove([path_interno])
            except Exception as e:
                print(f"Error al borrar archivo físico del Storage: {e}")

        supabase.table("polizas").delete().eq("id", poliza_id).execute()
        return {"status": "success", "message": "Documento eliminado correctamente."}
        
    except Exception as e:
        print(f"Error general al eliminar: {e}")
        return {"error": str(e)}

import io
import os
import re
import zipfile
from datetime import datetime
from fastapi import APIRouter, Form, Request, File, UploadFile, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from app.database import supabase
from postgrest.exceptions import APIError
import unicodedata
from urllib.parse import unquote, urlparse

router = APIRouter()

@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = supabase.table("perfiles").select("*").eq("username", username).execute()
    
    if not user.data:
        return RedirectResponse(url="/?error=Usuario+no+encontrado", status_code=303)
    
    if user.data[0]['password_hash'].strip() == password.strip():
        request.session["usuario_id"] = str(user.data[0]['id'])
        request.session["usuario_rol"] = str(user.data[0]['rol']).lower()
        request.session["usuario_nombre"] = str(user.data[0]['username']) 
        return RedirectResponse(url="/panel", status_code=303)
    else:
        return RedirectResponse(url="/?error=Contrasena+incorrecta", status_code=303)

@router.get("/buscar_poliza")
def buscar_poliza(request: Request, cliente: str = None, dia: str = None, mes: str = None, anio: str = None):
    if not request.session.get("usuario_id"):
        raise HTTPException(status_code=401, detail="No autorizado")

    query = supabase.table("polizas").select("*")
    if cliente and cliente.strip() and cliente != "undefined":
        term = f"%{cliente.strip()}%"
        query = query.ilike("cliente_nombre", term)
    if dia and dia.strip() and dia != "undefined":
        query = query.eq("dia", int(dia))
    if mes and mes.strip() and mes != "undefined":
        query = query.eq("mes", mes.strip().upper())
    if anio and anio.strip() and anio != "undefined":
        query = query.eq("anio", int(anio))
        
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
        file_path = f"{anio}/{mes_extraido}/{nombre_limpio_storage}"

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
            "cliente_nombre": nombre_cliente_limpio, # Nombre limpio para que lo busques fácilmente
            "dia": dia_extraido, 
            "mes": mes_extraido[:3], 
            "anio": anio, 
            "url_archivo": url_archivo,
            "path_storage": file_path, # Guarda el nombre único original con sus números
            "tipo": tipo
        }

        supabase.table("polizas").insert(data).execute()
        return {"message": "Exito"}
        
    except Exception as e:
        return {"error": str(e)}
        
@router.get("/usuarios_lista")
def listar_usuarios(request: Request):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        raise HTTPException(status_code=401, detail="No autorizado")
    return supabase.table("perfiles").select("*").execute().data

@router.delete("/borrar_usuario/{user_id}")
def borrar_usuario(request: Request, user_id: str):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        raise HTTPException(status_code=401, detail="No autorizado")
    supabase.table("perfiles").delete().eq("id", user_id).execute()
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
            supabase.table("perfiles").update(actualizacion).eq("id", user_id).execute()
            return RedirectResponse(url="/usuarios_pantalla", status_code=303)
        except APIError:
            continue
    return RedirectResponse(url="/usuarios_pantalla?error=Error+al+actualizar", status_code=303)

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
def descargar_anio(request: Request, anio: str):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        raise HTTPException(status_code=404, detail="No autorizado")
    
    query = supabase.table("polizas").select("*")
    if anio != "todos":
        query = query.eq("anio", int(anio))
        
    polizas = query.execute()
    if not polizas.data:
        raise HTTPException(status_code=404, detail="No hay pólizas para descargar")
        
    buffer = io.BytesIO()
    nombres_usados = set() # Para evitar el error de duplicados
    
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for p in polizas.data:
            try:
                path_interno = p.get('path_storage')
                if not path_interno:
                    url_path = urlparse(p['url_archivo']).path
                    path_interno = url_path.split("/object/public/polizas/")[1]
                    path_interno = unquote(path_interno)
                
                nombre_archivo = path_interno.split("/")[-1]
                
                # Creamos una ruta interna única evitando colisiones
                ruta_interna = f"{p['anio']}/{p['mes']}/{nombre_archivo}"
                
                # Si el nombre ya se usó, le agregamos un sufijo para no romper el ZIP
                if ruta_interna in nombres_usados:
                    nombre_base, ext = os.path.splitext(nombre_archivo)
                    ruta_interna = f"{p['anio']}/{p['mes']}/{nombre_base}_{p['id']}{ext}"
                
                nombres_usados.add(ruta_interna)
                
                archivo_binario = supabase.storage.from_("polizas").download(path_interno)
                zip_file.writestr(ruta_interna, archivo_binario)
            except Exception as e:
                print(f"Error saltando archivo: {e}")
                continue
                
    buffer.seek(0)
    nombre_zip = f"polizas_{anio}.zip" if anio != "todos" else "respaldo_total.zip"
    return StreamingResponse(
        io.BytesIO(buffer.getvalue()), 
        media_type="application/zip", 
        headers={"Content-Disposition": f"attachment; filename={nombre_zip}"}
    )

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

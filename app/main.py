import os
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app.routes.auth import router as auth_router

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key="tu_clave_secreta_super_segura_aqui")

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response

templates = Jinja2Templates(directory="app/templates")
app.include_router(auth_router, prefix="")

@app.get("/")
def login_pantalla(request: Request):
    if request.session.get("usuario_id"):
        return RedirectResponse(url="/panel", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": request.query_params.get("error")})

@app.get("/panel")
def panel_pantalla(request: Request):
    if not request.session.get("usuario_id"):
        return RedirectResponse(url="/?error=Inicia+sesion", status_code=303)
    return templates.TemplateResponse(request, "consultar.html", {
        "rol": request.session.get("usuario_rol"),
        "nombre": request.session.get("usuario_nombre")
    })
        
@app.get("/subir_pantalla")
def subir_pantalla(request: Request):
    if not request.session.get("usuario_id"):
        return RedirectResponse(url="/?error=Inicia+sesion", status_code=303)
    return templates.TemplateResponse(request, "subir.html", {
        "rol": request.session.get("usuario_rol"),
        "nombre": request.session.get("usuario_nombre")
    })

@app.get("/usuarios_pantalla")
def usuarios_pantalla(request: Request):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        return RedirectResponse(url="/panel?error=Acceso+denegado", status_code=303)
    return templates.TemplateResponse(request, "usuarios.html", {
        "rol": request.session.get("usuario_rol"),
        "nombre": request.session.get("usuario_nombre"),
        "error": request.query_params.get("error")
    })

@app.get("/mantenimiento_pantalla")
def mantenimiento_pantalla(request: Request):
    if not request.session.get("usuario_id") or request.session.get("usuario_rol") != "admin":
        return RedirectResponse(url="/panel?error=Acceso+denegado", status_code=303)
    return templates.TemplateResponse(request, "mantenimiento.html", {
        "rol": request.session.get("usuario_rol"),
        "nombre": request.session.get("usuario_nombre")
    })

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)

# Solución definitiva para Windows: Calcula la ubicación real de la carpeta static
base_dir = os.path.dirname(os.path.abspath(__file__))
ruta_estatica = os.path.join(base_dir, "static")
app.mount("/static", StaticFiles(directory=ruta_estatica), name="static")

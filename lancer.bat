@echo off
REM Lanceur Windows pour immo-scanner : double-cliquez sur ce fichier.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"

REM --- Trouver Python ---
set PY=
where py >nul 2>nul && set PY=py
if not defined PY (where python >nul 2>nul && set PY=python)
if not defined PY (
  echo Python n'est pas installe.
  echo Installez-le depuis https://www.python.org/downloads/ en cochant "Add Python to PATH",
  echo puis relancez ce fichier.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

REM --- Installation (une seule fois) ---
if not exist data\.installe (
  echo Installation en cours...
  %PY% -m pip install -e . || (echo Echec de l'installation. & pause & exit /b 1)
  if not exist data mkdir data
  echo ok> data\.installe
)

:menu
echo.
echo ================= IMMO-SCANNER =================
echo  --- DONNEES REELLES ---
echo  1. Telecharger les donnees reelles d'un departement (ventes + loyers)
echo  2. Classement des communes d'un departement
echo  3. Analyser un bien precis (ex: une annonce vue sur Leboncoin)
echo  4. Importer un fichier d'annonces (CSV/JSON) et ouvrir le rapport
echo  5. Ouvrir le rapport des annonces deja importees
echo  6. Importer un fichier de loyers manuellement
echo  7. Etat de la base
echo  --- TEST ---
echo  9. Demo (donnees FICTIVES, base separee)
echo  Q. Quitter
echo ================================================
set CHOIX=
set /p CHOIX=Votre choix : 

if "%CHOIX%"=="1" goto dvf
if "%CHOIX%"=="2" goto marches
if "%CHOIX%"=="3" goto estimer
if "%CHOIX%"=="4" goto import
if "%CHOIX%"=="5" goto rapport
if "%CHOIX%"=="6" goto loyers
if "%CHOIX%"=="7" goto stats
if "%CHOIX%"=="9" goto demo
if /i "%CHOIX%"=="Q" exit /b 0
goto menu

:demo
%PY% -m immo_scanner.cli --db data\demo.db demo --sortie rapport_demo.html
start "" rapport_demo.html
goto menu

:dvf
set DEP=
set /p DEP=Numero(s) de departement separes par un espace (ex: 33 ou 33 40 64, "all" = toute la France) : 
echo Telechargement en cours (quelques minutes par departement)...
%PY% -m immo_scanner.cli preparer %DEP%
goto menu

:marches
set DEP=
set /p DEP=Departement (ex: 33) : 
set TYPE=
set /p TYPE=Type (appartement / maison) [appartement] : 
if "%TYPE%"=="" set TYPE=appartement
%PY% -m immo_scanner.cli marches --departements %DEP% --type %TYPE%
goto menu

:estimer
set PRIX=
set SURF=
set CP=
set VILLE=
set DPE=
set TYPE=
set /p PRIX=Prix en euros (ex: 145000) : 
set /p SURF=Surface en m2 (ex: 42) : 
set /p CP=Code postal (ex: 33000) : 
set /p VILLE=Ville (ex: Bordeaux) : 
set /p TYPE=Type (appartement / maison) [appartement] : 
set /p DPE=DPE (A a G, vide si inconnu) : 
if "%TYPE%"=="" set TYPE=appartement
if "%DPE%"=="" (
  %PY% -m immo_scanner.cli estimer --prix %PRIX% --surface %SURF% --cp %CP% --ville "%VILLE%" --type %TYPE%
) else (
  %PY% -m immo_scanner.cli estimer --prix %PRIX% --surface %SURF% --cp %CP% --ville "%VILLE%" --type %TYPE% --dpe %DPE%
)
goto menu

:import
set FICHIER=
set /p FICHIER=Chemin du fichier (glissez-deposez le fichier ici puis Entree) : 
%PY% -m immo_scanner.cli import %FICHIER%
goto rapport

:rapport
%PY% -m immo_scanner.cli rapport --sortie rapport.html
start "" rapport.html
goto menu

:loyers
set FICHIER=
set TYPE=
set /p FICHIER=Chemin du fichier de loyers (glissez-deposez ici) : 
set /p TYPE=Ce fichier concerne (appartement / maison) : 
%PY% -m immo_scanner.cli loyers %FICHIER% --type %TYPE%
goto menu

:stats
%PY% -m immo_scanner.cli stats
goto menu

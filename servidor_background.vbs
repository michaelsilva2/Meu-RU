Dim oShell
Set oShell = CreateObject("WScript.Shell")
oShell.Run "cmd /c cd /d C:\Users\Michael\MeuRU_-SRC\ru_system && python -m uvicorn main:app --host 0.0.0.0 --port 8000", 0, False
oShell.Run "cmd /c C:\Users\Michael\AppData\Local\ngrok\ngrok.exe http 8000", 0, False
Set oShell = Nothing

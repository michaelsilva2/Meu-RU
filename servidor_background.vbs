Dim oShell, oFSO, scriptDir, ngrokPath

Set oShell = CreateObject("WScript.Shell")
Set oFSO = CreateObject("Scripting.FileSystemObject")
scriptDir = oFSO.GetParentFolderName(WScript.ScriptFullName)

oShell.Run "cmd /c cd /d """ & scriptDir & "\ru_system"" && python -m uvicorn main:app --host 0.0.0.0 --port 8000", 0, False

' ngrok e opcional: usa o executavel dentro da propria pasta do projeto se existir,
' senao tenta o ngrok do PATH do sistema. Se nenhum dos dois existir, so nao sobe o tunel.
ngrokPath = scriptDir & "\ngrok.exe"
If oFSO.FileExists(ngrokPath) Then
    oShell.Run "cmd /c """ & ngrokPath & """ http 8000", 0, False
Else
    oShell.Run "cmd /c where ngrok >nul 2>&1 && ngrok http 8000", 0, False
End If

Set oShell = Nothing
Set oFSO = Nothing

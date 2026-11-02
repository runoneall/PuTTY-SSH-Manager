Option Explicit

Dim fso, shell, scriptDir, cmdLine, venvDir
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir

venvDir = scriptDir & "\.venv"
If fso.FolderExists(venvDir) Then
    cmdLine = "cmd /c " & _
        "cd /d """ & scriptDir & """ && " & _
        "call "".venv\Scripts\activate.bat"" && " & _
        """python"" ""PuTTY SSH Manager.py"""

Else
    cmdLine = "cmd /c " & _
        "cd /d """ & scriptDir & """ && " & _
        "python3 -m venv .venv && " & _
        "call "".venv\Scripts\activate.bat"" && " & _
        "python -m pip install --upgrade pip && " & _
        "python -m pip install pywinauto && " & _
        """python"" ""PuTTY SSH Manager.py"""
End If

shell.Run cmdLine, 0, False

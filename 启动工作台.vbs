Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & root & "\.venv\Scripts\pythonw.exe"" """ & root & "\scripts\launch_workbench.py""", 0, False

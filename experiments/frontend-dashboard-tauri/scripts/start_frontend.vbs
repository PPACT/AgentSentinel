' Hidden launcher for start_frontend.bat.
' Runs the dev console without any console window, waits for it to exit,
' and only shows a message box when startup fails (normal app close is silent).
' NOTE: keep this file ASCII-only. cscript reads .vbs as ANSI, so UTF-8 Chinese
' would be garbled and can break parsing on a cp936 system.
Option Explicit

Dim fso, shell, bat, logPath, rc
If WScript.Arguments.Count = 0 Then WScript.Quit 2

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

bat = fso.GetAbsolutePathName(WScript.Arguments(0))
logPath = fso.GetAbsolutePathName(fso.BuildPath(fso.GetParentFolderName(bat), "..\..\.claude\logs\frontend-dev.log"))

' window style 0 = hidden; True = wait for the dev process to exit.
rc = shell.Run("cmd.exe /d /c call """ & bat & """ --hidden", 0, True)

Select Case rc
  Case 0
    ' Normal exit: the user closed the app window.
  Case 2
    MsgBox "AgentSentinel Console is already running. Close the existing window first.", vbInformation, "AgentSentinel Console"
  Case 3
    MsgBox "Port 1420 is already in use (stale Vite process?). Close it and retry.", vbExclamation, "AgentSentinel Console"
  Case Else
    MsgBox "AgentSentinel Console failed to start (exit " & rc & ")." & vbCrLf & vbCrLf & _
      "Log: " & logPath, vbExclamation, "AgentSentinel Console"
End Select

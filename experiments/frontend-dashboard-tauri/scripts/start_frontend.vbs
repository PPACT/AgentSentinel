' Hidden launcher for start_frontend.bat.
' Runs the dev console without any console window, waits for it to exit, and
' shows a diagnostic box only for real failures. A normal window close is silent.
' NOTE: keep this file ASCII-only. cscript reads .vbs as ANSI, so UTF-8 Chinese
' would be garbled and can break parsing on a cp936 system.
Option Explicit

Dim fso, shell, bat, logPath, statePath, rc, state, detail, msg, title
If WScript.Arguments.Count = 0 Then WScript.Quit 2

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

bat = fso.GetAbsolutePathName(WScript.Arguments(0))
logPath = fso.GetAbsolutePathName(fso.BuildPath(fso.GetParentFolderName(bat), "..\..\.claude\logs\frontend-dev.log"))
statePath = logPath & ".state"

' window style 0 = hidden; True = wait for the dev process to exit.
rc = shell.Run("cmd.exe /d /c call """ & bat & """ --hidden", 0, True)

Select Case rc
  Case 0
    ' Normal exit: the user closed the app window.
  Case 2
    MsgBox "AgentSentinel Console is already running. Close the existing window first.", vbInformation, "AgentSentinel Console"
  Case 3
    MsgBox "Port 1420 is already in use (stale Vite process?). Close it and retry.", vbExclamation, "AgentSentinel Console"
  Case 4
    MsgBox "npm was not found in PATH. Install Node.js or open a terminal where npm is available.", vbExclamation, "AgentSentinel Console"
  Case 5
    MsgBox "cargo was not found. Install Rust (rustup) or add %USERPROFILE%\.cargo\bin to PATH.", vbExclamation, "AgentSentinel Console"
  Case Else
    state = ReadState(statePath)
    detail = Trim(StripAnsi(ReadTail(logPath, 1400)))
    If Left(state, 7) = "exited:" Then
      title = "AgentSentinel Console exited"
      msg = "The app started, then exited (exit " & rc & ")." & vbCrLf & _
            "If you closed the window yourself, this message can be ignored."
    Else
      title = "AgentSentinel Console failed to start"
      msg = "The dev process exited before the app window started (exit " & rc & ")." & vbCrLf & _
            "Check whether ports 8765/1420 are used by another process and inspect the log tail."
    End If
    If Len(detail) > 0 Then
      If Len(detail) > 700 Then detail = Right(detail, 700)
      msg = msg & vbCrLf & vbCrLf & "Recent log:" & vbCrLf & detail
    End If
    msg = msg & vbCrLf & vbCrLf & "Log: " & logPath
    MsgBox msg, vbExclamation, title
End Select

Function ReadState(path)
  Dim f, text
  ReadState = ""
  If Not fso.FileExists(path) Then Exit Function
  On Error Resume Next
  Set f = fso.OpenTextFile(path, 1, False)
  If Err.Number <> 0 Then
    Err.Clear
    Exit Function
  End If
  text = f.ReadAll
  f.Close
  On Error GoTo 0
  ReadState = Trim(text)
End Function

Function ReadTail(path, maxChars)
  Dim stream, text
  ReadTail = ""
  If Not fso.FileExists(path) Then Exit Function

  On Error Resume Next
  Set stream = CreateObject("ADODB.Stream")
  If Err.Number <> 0 Then
    Err.Clear
    Exit Function
  End If
  stream.Type = 2
  stream.Charset = "utf-8"
  stream.Open
  stream.LoadFromFile path
  text = stream.ReadText(-1)
  stream.Close
  If Err.Number <> 0 Then
    Err.Clear
    Exit Function
  End If
  On Error GoTo 0

  If Len(text) > maxChars Then text = Right(text, maxChars)
  ReadTail = text
End Function

Function StripAnsi(text)
  Dim re
  Set re = New RegExp
  re.Global = True
  re.IgnoreCase = True
  re.Pattern = Chr(27) & "\[[0-9;]*[A-Za-z]"
  StripAnsi = re.Replace(text, "")
End Function

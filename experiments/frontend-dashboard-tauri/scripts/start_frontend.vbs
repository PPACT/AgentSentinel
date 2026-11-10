' Hidden launcher for start_frontend.bat.
' Runs the dev console without any console window, waits for it to exit, and
' shows a diagnostic box only for real failures. A normal window close is silent.
' NOTE: keep this file ASCII-only. cscript reads .vbs as ANSI, so UTF-8 Chinese
' would be garbled and can break parsing on a cp936 system.
Option Explicit

Dim fso, shell, bat, logsDir, currentPath, defaultLogPath, logPath, statePath
Dim rc, state, detail
If WScript.Arguments.Count = 0 Then WScript.Quit 2

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

bat = fso.GetAbsolutePathName(WScript.Arguments(0))
logsDir = fso.GetAbsolutePathName(fso.BuildPath(fso.GetParentFolderName(bat), "..\..\.claude\logs"))
currentPath = fso.BuildPath(logsDir, "frontend-dev.current")
defaultLogPath = fso.BuildPath(logsDir, "frontend-dev.log")
logPath = ReadCurrentLog(currentPath, defaultLogPath)

LogLaunch "vbs start bat=" & bat

' window style 0 = hidden; True = wait for the dev process to exit.
On Error Resume Next
rc = shell.Run("cmd.exe /d /c call """ & bat & """ --hidden", 0, True)
If Err.Number <> 0 Then
  LogLaunch "vbs shell.Run error " & Err.Number & ": " & Err.Description
  MsgBox "Launcher failed before the app started (error " & Err.Number & ")." & vbCrLf & _
    Err.Description & vbCrLf & vbCrLf & "Log: " & logPath, vbExclamation, "AgentSentinel Console"
  WScript.Quit 1
End If
On Error GoTo 0

' The bat writes a unique run log and points frontend-dev.current at it.
logPath = ReadCurrentLog(currentPath, defaultLogPath)
statePath = logPath & ".state"
state = ReadState(statePath)
LogLaunch "vbs end rc=" & rc & " state=" & state

Select Case rc
  Case 2
    MsgBox "AgentSentinel Console is already running. Close the existing window first.", vbInformation, "AgentSentinel Console"
  Case 3
    MsgBox "Port 1420 is already in use (stale Vite process?). Close it and retry.", vbExclamation, "AgentSentinel Console"
  Case 4
    MsgBox "npm was not found in PATH. Install Node.js or open a terminal where npm is available.", vbExclamation, "AgentSentinel Console"
  Case 5
    MsgBox "cargo was not found. Install Rust (rustup) or add %USERPROFILE%\.cargo\bin to PATH.", vbExclamation, "AgentSentinel Console"
  Case 6
    MsgBox "Could not create the run log under .claude\logs. Check write permission or disk space.", vbExclamation, "AgentSentinel Console"
  Case 0
    If Left(state, 7) <> "exited:" Then
      detail = Trim(StripAnsi(ReadTail(logPath, 1400)))
      ShowFailure "AgentSentinel Console failed to start", _
        "The dev process exited (exit 0) before the app window started." & vbCrLf & _
        "The log may be empty or locked; check frontend-launch.log and the run log below.", detail, logPath
    End If
  Case Else
    detail = Trim(StripAnsi(ReadTail(logPath, 1400)))
    If Left(state, 7) = "exited:" Then
      ShowFailure "AgentSentinel Console exited", _
        "The app started, then exited (exit " & rc & ")." & vbCrLf & _
        "If you closed the window yourself, this message can be ignored.", detail, logPath
    Else
      ShowFailure "AgentSentinel Console failed to start", _
        "The dev process exited before the app window started (exit " & rc & ")." & vbCrLf & _
        "Check whether ports 8765/1420 are used by another process and inspect the log tail.", detail, logPath
    End If
End Select

Sub ShowFailure(title, message, detail, logPath)
  Dim msg
  msg = message
  If Len(detail) > 0 Then
    If Len(detail) > 700 Then detail = Right(detail, 700)
    msg = msg & vbCrLf & vbCrLf & "Recent log:" & vbCrLf & detail
  End If
  msg = msg & vbCrLf & vbCrLf & "Log: " & logPath
  MsgBox msg, vbExclamation, title
End Sub

Function ReadCurrentLog(path, fallback)
  Dim f, text
  ReadCurrentLog = fallback
  If Not fso.FileExists(path) Then Exit Function
  On Error Resume Next
  Set f = fso.OpenTextFile(path, 1, False)
  If Err.Number <> 0 Then
    Err.Clear
    Exit Function
  End If
  text = Trim(f.ReadAll)
  f.Close
  On Error GoTo 0
  If Len(text) > 0 Then ReadCurrentLog = text
End Function

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

Sub LogLaunch(message)
  Dim path, f
  path = fso.GetAbsolutePathName(fso.BuildPath(fso.GetParentFolderName(bat), "..\..\.claude\logs\frontend-launch.log"))
  On Error Resume Next
  Set f = fso.OpenTextFile(path, 8, True)
  f.WriteLine Now & " " & message
  f.Close
  On Error GoTo 0
End Sub

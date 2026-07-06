Set oShell = CreateObject("WScript.Shell")
Set oExec = oShell.Exec("cmd /c cd /d C:\Users\201397\local-competitor-intelligence && git push origin main 2>&1")
Dim output
output = ""
Do While Not oExec.StdOut.AtEndOfStream
    output = output & oExec.StdOut.ReadLine() & vbCrLf
Loop
' Write result to file
Dim fso, f
Set fso = CreateObject("Scripting.FileSystemObject")
Set f = fso.CreateTextFile("C:\Users\201397\local-competitor-intelligence\push_result.txt", True)
f.Write output
f.Close
MsgBox "Git push result:" & vbCrLf & vbCrLf & output, 64, "Push Result"

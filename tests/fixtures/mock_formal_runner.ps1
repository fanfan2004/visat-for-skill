param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Remaining
)

$index = [Array]::IndexOf($Remaining, "-TaskId")
$taskId = $Remaining[$index + 1]
Write-Output ("MOCK_STARTED:" + $taskId)
if ($taskId -eq "task-002") { exit 1 }
exit 0

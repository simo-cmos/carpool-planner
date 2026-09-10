param(
    [string]$Title = "Drivers Manager",
    [string]$Message = "New activity detected.",
    [int]$TimeoutMs = 9000
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Show-VisiblePopupForm {
    param(
        [string]$PopupTitle,
        [string]$PopupMessage
    )

    $form = New-Object System.Windows.Forms.Form
    $form.Text = $PopupTitle
    $form.StartPosition = "CenterScreen"
    $form.TopMost = $true
    $form.Width = 520
    $form.Height = 220
    $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.BackColor = [System.Drawing.Color]::FromArgb(255, 249, 236)

    $titleLabel = New-Object System.Windows.Forms.Label
    $titleLabel.Text = $PopupTitle
    $titleLabel.AutoSize = $false
    $titleLabel.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
    $titleLabel.Location = New-Object System.Drawing.Point(18, 18)
    $titleLabel.Size = New-Object System.Drawing.Size(470, 32)

    $messageLabel = New-Object System.Windows.Forms.Label
    $messageLabel.Text = $PopupMessage
    $messageLabel.AutoSize = $false
    $messageLabel.Font = New-Object System.Drawing.Font("Segoe UI", 11)
    $messageLabel.Location = New-Object System.Drawing.Point(18, 62)
    $messageLabel.Size = New-Object System.Drawing.Size(470, 72)

    $closeButton = New-Object System.Windows.Forms.Button
    $closeButton.Text = "Close"
    $closeButton.Width = 100
    $closeButton.Height = 34
    $closeButton.Location = New-Object System.Drawing.Point(388, 138)
    $closeButton.Add_Click({ $form.Close() })

    $form.Controls.Add($titleLabel)
    $form.Controls.Add($messageLabel)
    $form.Controls.Add($closeButton)

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = [Math]::Max($TimeoutMs, 5000)
    $timer.Add_Tick({
        $timer.Stop()
        $form.Close()
    })
    $timer.Start()

    [void]$form.ShowDialog()
}

Show-VisiblePopupForm -PopupTitle $Title -PopupMessage $Message

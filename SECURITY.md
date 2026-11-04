# Security

## Reporting a problem
Please report a security problem privately, through GitHub's
[private vulnerability reporting](https://github.com/Corteil/mpexplorer/security/advisories/new) for this repository,
not as a public issue. Ordinary bugs go in [issues](https://github.com/Corteil/mpexplorer/issues).

## What to know when using mpexplorer
- **Flash backups can contain secrets.** A backup made with Tools > Back up flash is a copy of the whole flash, which can
  include saved Wi-Fi names and passwords and other files on the board. mpexplorer saves backups readable only by your
  user. Do not commit them to a repository, attach them to issues or share them. (`Backups/`, `*.uf2` and `*.bin` are
  in this repository's `.gitignore` for that reason.)
- **The firmware tools write and erase flash.** They only act on a board in its bootloader and only when you start them.
  Check the chip and image shown in the dialog before you confirm.
- **Downloads.** Libraries and firmware are fetched over HTTPS. A custom source that is not GitHub is trusted only after
  you say so: add only sources you trust, because their files are written to your board.
- **Nothing is installed on the board** in normal use. Files you copy to it are yours to check.

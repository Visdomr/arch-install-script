#!/usr/bin/env python3

import os
import subprocess
import sys
import parted
import getpass
import re

# Ensure the script runs as root
if os.geteuid() != 0:
    print("This script must be run as root. Please use sudo or switch to root.")
    sys.exit(1)

# Utility function to run shell commands
def run_command(command, check=True):
    try:
        result = subprocess.run(command, shell=True, check=check, text=True, capture_output=True)
        return result
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {command}")
        print(f"Output: {e.stderr}")
        raise

# Detect UEFI mode
def detect_uefi():
    return os.path.exists("/sys/firmware/efi")

# Check for internet connectivity
def check_internet():
    print("Checking internet connectivity...")
    try:
        run_command("ping -c 1 archlinux.org")
        print("Internet connection confirmed.")
        return True
    except subprocess.CalledProcessError:
        print("No internet connection detected.")
        return False

# Configure network
def configure_network():
    if check_internet():
        return
    print("Network configuration required.")
    choice = input("Select network setup: [1] DHCP (wired), [2] Wi-Fi, [3] Skip: ").strip()
    
    if choice == "1":
        run_command("dhcpcd")
        if check_internet():
            print("Network configured via DHCP.")
        else:
            print("DHCP failed. Please configure manually or try Wi-Fi.")
            sys.exit(1)
    elif choice == "2":
        print("Scanning for Wi-Fi networks...")
        run_command("iwctl device list")
        device = input("Enter wireless device name (e.g., wlan0): ").strip()
        run_command(f"iwctl station {device} scan")
        run_command(f"iwctl station {device} get-networks")
        ssid = input("Enter SSID: ").strip()
        password = getpass.getpass("Enter Wi-Fi password (leave blank for none): ")
        if password:
            run_command(f"iwctl station {device} connect '{ssid}' --passphrase '{password}'")
        else:
            run_command(f"iwctl station {device} connect '{ssid}'")
        if check_internet():
            print("Wi-Fi configured successfully.")
        else:
            print("Wi-Fi connection failed. Please check credentials or configure manually.")
            sys.exit(1)
    else:
        print("Skipping network configuration. Installation may fail without internet.")
        sys.exit(1)

# List available disks
def list_disks():
    print("Detecting available disks...")
    disks = []

# Manual partitioning with cfdisk
def manual_partitioning(disk):
    print(f"Launching cfdisk for manual partitioning on /dev/{disk}...")
    run_command(f"cfdisk /dev/{disk}")
    print("Manual partitioning completed. Please verify your partitions with 'lsblk'.")
    return input("Enter the partitions (e.g., /dev/sda1 /dev/sda2): ").strip().split()

# Automatic partitioning
def auto_partition_disk(disk, uefi, swap=False):
    print(f"Partitioning disk: /dev/{disk}")
    device = parted.getDevice(f"/dev/{disk}")
    disk_obj = parted.freshDisk(device, 'gpt')
    partitions = []

    # EFI partition (UEFI only)
    if uefi:
        efi_size = 512 * 1024 * 1024  # 512MB
        efi_geometry = parted.Geometry(device, start=1, length=device.getLength() * efi_size // device.sectorSize)
        efi_part = parted.Partition(disk=disk_obj, type=parted.PARTITION_NORMAL, fs=parted.FileSystem(type='fat32', geometry=efi_geometry))
        efi_part.setFlag(parted.PARTITION_BOOT)
        disk_obj.addPartition(partition=efi_part, constraint=device.optimalAlignedConstraint)
        start = efi_geometry.end + 1
    else:
        start = 1

    # Swap partition (if selected)
    if swap:
        swap_size = 2 * 1024 * 1024 * 1024  # 2GB
        swap_geometry = parted.Geometry(device, start=start, length=device.getLength() * swap_size // device.sectorSize)
        swap_part = parted.Partition(disk=disk_obj, type=parted.PARTITION_NORMAL, fs=parted.FileSystem(type='swap', geometry=swap_geometry))
        disk_obj.addPartition(partition=swap_part, constraint=device.optimalAlignedConstraint)
        start = swap_geometry.end + 1

    # Root partition (rest of the disk)
    root_geometry = parted.Geometry(device, start=start, length=device.getLength() - start - 1)
    root_part = parted.Partition(disk=disk_obj, type=parted.PARTITION_NORMAL, fs=parted.FileSystem(type='ext4', geometry=root_geometry))
    disk_obj.addPartition(partition=root_part, constraint=device.optimalAlignedConstraint)

    disk_obj.commit()
    print("Disk partitioning completed.")

    if uefi and swap:
        return [f"/dev/{disk}1", f"/dev/{disk}2", f"/dev/{disk}3"]
    elif uefi or swap:
        return [f"/dev/{disk}1", f"/dev/{disk}2"]
    else:
        return [f"/dev/{disk}1"]

# Partitioning choices
def partition_disk(disk, uefi):
    print(f"Selected disk: {disk}")
    print("Partitioning options:")
    print("[1] Automatic: /boot (512MB, UEFI only) + / (rest)")
    print("[2] Automatic with swap: /boot (512MB, UEFI only) + swap (2GB) + / (rest)")
    print("[3] Manual partitioning with cfdisk")
    choice = input("Select partitioning option [1-3]: ").strip()

    if choice == "1":
        return auto_partition_disk(disk, uefi, swap=False)
    elif choice == "2":
        return auto_partition_disk(disk, uefi, swap=True)
    elif choice == "3":
        return manual_partitioning(disk)
    else:
        print("Invalid choice. Defaulting to option 1.")
        return auto_partition_disk(disk, uefi, swap=False)

# Format partitions
def format_partitions(partitions, uefi):
    print("Formatting partitions...")
    for i, part in enumerate(partitions):
        if uefi and i == 0:  # EFI partition
            run_command(f"mkfs.fat -F32 {part}")
        elif (len(partitions) == 3 and i == 1) or (not uefi and len(partitions) == 2 and i == 0):  # Swap
            run_command(f"mkswap {part}")
            run_command(f"swapon {part}")
        else:  # Root or other
            run_command(f"mkfs.ext4 {part}")
    print("Partitions formatted.")

# Mount partitions
def mount_partitions(partitions, uefi):
    print("Mounting partitions...")
    root_part = partitions[-1]  # Last partition is always /
    run_command(f"mount {root_part} /mnt")
    if uefi:
        efi_part = partitions[0]
        run_command("mkdir -p /mnt/boot")
        run_command(f"mount {efi_part} /mnt/boot")
    print("Partitions mounted.")

# Setup Pacman
def setup_pacman():
    print("Setting up Pacman...")
    run_command("pacman-key --init")
    run_command("pacman-key --populate archlinux")
    run_command("echo 'Server = https://geo.mirror.pkgbuild.com/\$repo/os/\$arch' > /mnt/etc/pacman.d/mirrorlist")
    run_command("pacman -Sy")

# Install base system
def install_base_system():
    print("Installing base system...")
    run_command("pacstrap /mnt base linux linux-firmware")
    print("Base system installed.")

# Install desktop environment
def install_desktop_environment():
    desktops = {
        "1": ("GNOME", "gnome gnome-shell", "gdm"),
        "2": ("KDE Plasma", "plasma kde-applications", "sddm"),
        "3": ("XFCE", "xfce4 xfce4-goodies lightdm lightdm-gtk-greeter", "lightdm"),
        "4": ("COSMIC (System76)", "cosmic-session", "cosmic-session"),
        "5": ("MATE", "mate mate-extra", "lightdm"),
        "6": ("Cinnamon", "cinnamon lightdm lightdm-gtk-greeter", "lightdm"),
        "7": ("LXQt", "lxqt lightdm lightdm-gtk-greeter", "lightdm"),
        "8": ("None", "", "")
    }
    print("Desktop environment options:")
    for key, (name, _, _) in desktops.items():
        print(f"[{key}] {name}")
    choice = input("Select desktop environment [1-8]: ").strip() or "8"

    if choice in desktops and choice != "8":
        name, packages, dm = desktops[choice]
        print(f"Installing {name}...")
        run_command(f"arch-chroot /mnt pacman -S --noconfirm {packages} xorg")
        if dm:
            run_command(f"arch-chroot /mnt systemctl enable {dm}")
        print(f"{name} installed and configured.")
    else:
        print("No desktop environment will be installed.")

# Generate fstab
def generate_fstab():
    print("Generating fstab...")
    run_command("genfstab -U /mnt >> /mnt/etc/fstab")
    print("fstab generated.")

# Install bootloader
def install_bootloader(disk, uefi):
    if not uefi:
        print("BIOS detected. Only GRUB (BIOS) is supported.")
        choice = "1"
    else:
        print("UEFI detected. Bootloader options:")
        print("[1] GRUB (UEFI)")
        print("[2] systemd-boot")
        print("[3] rEFInd")
        choice = input("Select bootloader [1-3]: ").strip() or "1"

    chroot_cmd = "arch-chroot /mnt /bin/bash -c '{}'"
    if choice == "1":
        print("Installing GRUB...")
        run_command(chroot_cmd.format("pacman -S --noconfirm grub"))
        if uefi:
            run_command(chroot_cmd.format("pacman -S --noconfirm efibootmgr"))
            run_command(chroot_cmd.format(f"grub-install --target=x86_64-efi --efi-directory=/boot --bootloader-id=GRUB"))
        else:
            run_command(chroot_cmd.format(f"grub-install --target=i386-pc /dev/{disk}"))
        run_command(chroot_cmd.format("grub-mkconfig -o /boot/grub/grub.cfg"))
    elif choice == "2" and uefi:
        print("Installing systemd-boot...")
        run_command(chroot_cmd.format("pacman -S --noconfirm efibootmgr"))
        run_command(chroot_cmd.format("bootctl --path=/boot install"))
        with open("/mnt/boot/loader/loader.conf", "w") as f:
            f.write("default arch\n")
            f.write("timeout 3\n")
            f.write("editor 0\n")
        root_uuid = subprocess.check_output(f"blkid -s UUID -o value /dev/{disk}2", shell=True).decode().strip()
        with open("/mnt/boot/loader/entries/arch.conf", "w") as f:
            f.write("title Arch Linux\n")
            f.write("linux /vmlinuz-linux\n")
            f.write("initrd /initramfs-linux.img\n")
            f.write(f"options root=UUID={root_uuid} rw\n")
    elif choice == "3" and uefi:
        print("Installing rEFInd...")
        run_command(chroot_cmd.format("pacman -S --noconfirm refind"))
        run_command(chroot_cmd.format("refind-install"))
    else:
        print("Invalid choice or not supported in BIOS mode. Defaulting to GRUB.")
        install_bootloader(disk, uefi)  # Recursive call with default

    print("Bootloader installed.")

# Configure system
def configure_system(disk, uefi, hostname, username, password):
    print("Configuring system...")
    chroot_cmd = "arch-chroot /mnt /bin/bash -c '{}'"
    
    # Set hostname
    run_command(chroot_cmd.format(f"echo {hostname} > /etc/hostname"))
    run_command(chroot_cmd.format(f"echo '127.0.0.1 localhost' >> /etc/hosts"))
    run_command(chroot_cmd.format(f"echo '::1       localhost' >> /etc/hosts"))
    run_command(chroot_cmd.format(f"echo '127.0.1.1 {hostname}.localdomain {hostname}' >> /etc/hosts"))

    # Set timezone (default to UTC)
    run_command(chroot_cmd.format("ln -sf /usr/share/zoneinfo/UTC /etc/localtime"))
    run_command(chroot_cmd.format("hwclock --systohc"))

    # Set locale
    run_command(chroot_cmd.format("echo 'en_US.UTF-8 UTF-8' >> /etc/locale.gen"))
    run_command(chroot_cmd.format("locale-gen"))
    run_command(chroot_cmd.format("echo 'LANG=en_US.UTF-8' > /etc/locale.conf"))

    # Set root password
    run_command(chroot_cmd.format(f"echo 'root:{password}' | chpasswd"))

    # Create user
    run_command(chroot_cmd.format(f"useradd -m -G wheel {username}"))
    run_command(chroot_cmd.format(f"echo '{username}:{password}' | chpasswd"))
    run_command(chroot_cmd.format("sed -i 's/# %wheel ALL=(ALL:ALL) ALL/%wheel ALL=(ALL:ALL) ALL/' /etc/sudoers"))

    # Enable network services
    run_command(chroot_cmd.format("pacman -S --noconfirm networkmanager"))
    run_command(chroot_cmd.format("systemctl enable NetworkManager"))

    # Install bootloader
    install_bootloader(disk, uefi)

    print("System configuration completed.")

# Main installation flow
def main():
    print("Arch Linux Installation Script")
    print("=================================")

    # Detect UEFI
    uefi = detect_uefi()
    print(f"Boot mode: {'UEFI' if uefi else 'BIOS'}")

    # Configure network
    configure_network()

    # Select disk
    disks = list_disks()
    print("Available disks:", disks)
    disk = input("Select a disk (e.g., sda, nvme0n1): ").strip()
    if f"/dev/{disk}" not in [f"/dev/{d}" for d in disks]:
        print("Invalid disk selected.")
        sys.exit(1)

    # Partition and format
    partitions = partition_disk(disk, uefi)
    format_partitions(partitions, uefi)
    mount_partitions(partitions, uefi)

    # Setup Pacman and install base system
    setup_pacman()
    install_base_system()
    generate_fstab()

    # Install desktop environment
    install_desktop_environment()

    # User input for configuration
    hostname = input("Enter hostname: ").strip() or "archlinux"
    username = input("Enter username: ").strip()
    password = getpass.getpass("Enter password: ")

    # Configure system
    configure_system(disk, uefi, hostname, username, password)

    # Unmount and finish
    run_command("umount -R /mnt")
    print("Installation completed! You can now reboot into your new Arch Linux system.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInstallation aborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)

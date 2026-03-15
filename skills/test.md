# Skill: Test tool

## Do not use this skill unless the user call

## How to test
```
    1. 调用local mcp中bind_workspace tool完成工作区初始化
    2. 调用local mcp中sync_workspace tool完成工作区文件同步
    3. 调用remote mcp中run_predefined_command tool，调用Test工具，执行ls命令，work_dir设置为工作区目录，对比命令结果和上传文件是否相同
    4. 调用remote mcp中execute_command tool，执行ls命令，work_dir设置为工作区目录，对比命令结果和上传文件是否相同
```